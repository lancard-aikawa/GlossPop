// 辞書リンクの吹き出し。ホバー / フォーカスで表示、クリックで固定表示。
// 同じ表記がカテゴリ違いで複数登録されている場合はアコーディオンで並べる。
import { api, esc } from "./base.js";

const OPEN_DELAY = 130;
const CLOSE_DELAY = 220;
const GAP = 8;

const cache = new Map(); // 表記 -> lookup レスポンス
let pop = null;
let openTimer = null;
let closeTimer = null;
let current = null; // 表示中のアンカー (位置の基準)
let currentSurface = ""; // いま出している表記
let trail = []; // 吹き出しの中の語を辿った履歴 (← 戻る 用)
let pinned = false;
let seq = 0;

function ensurePop() {
  if (pop) return pop;
  pop = document.createElement("div");
  pop.className = "gloss-pop";
  pop.hidden = true;
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "用語解説");
  pop.addEventListener("pointerenter", () => clearTimeout(closeTimer));
  pop.addEventListener("pointerleave", () => {
    if (!pinned) scheduleHide();
  });
  pop.addEventListener("click", (ev) => {
    // **吹き出しの中の用語は、この吹き出しの中で開く。** 新しく開こうとすると、
    // `paint()` が innerHTML を差し替えて**いま指している要素ごと消える** ——
    // 指す先を失ったポインタが離脱扱いになり、元の吹き出しまで一緒に閉じる
    // （「両方消える」の正体がこれ）。位置の基準は元のアンカーのまま辿る
    const inner = ev.target.closest("a.gloss-link");
    if (inner) {
      ev.preventDefault();
      ev.stopPropagation();
      return drill(inner.dataset.gloss || inner.textContent);
    }
    // **それ以外のリンクは外へ通す。** 止めると、覆いを開く仕掛け（`overlay.js` の
    // document 上の listener）まで届かず、「辞書ページを開く →」だけが
    // ページ移動になる（＝読んでいた本文が捨てられる）
    if (ev.target.closest("a[href]")) return hide();
    // アコーディオンを開閉しても閉じないよう、それ以外は外に伝えない
    ev.stopPropagation();
  });
  document.body.append(pop);
  return pop;
}

function scheduleHide() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(hide, CLOSE_DELAY);
}

function hide() {
  clearTimeout(openTimer);
  clearTimeout(closeTimer);
  pinned = false;
  current = null;
  currentSurface = "";
  trail = [];
  if (pop) pop.hidden = true;
}

/** 吹き出しの中の語へ辿る。**押して辿ったのだから、離しても消えない。** */
function drill(surface) {
  const term = (surface || "").trim();
  if (!term || !current) return;
  trail.push(currentSurface);
  pinned = true;
  show(current, term);
}

/** 1 つ前の語へ戻る。辿れる道が無ければ何もしない。 */
function back() {
  if (!trail.length || !current) return;
  show(current, trail.pop());
}

function place(anchor) {
  const node = ensurePop();
  node.hidden = false;
  // 高さを測るために一旦左上へ寄せてから決める
  node.style.left = "0px";
  node.style.top = "0px";
  const rect = anchor.getBoundingClientRect();
  const box = node.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left = rect.left;
  if (left + box.width > vw - 12) left = vw - box.width - 12;
  if (left < 12) left = 12;

  const below = vh - rect.bottom - GAP;
  const above = rect.top - GAP;
  let top;
  if (box.height <= below || below >= above) {
    top = rect.bottom + GAP;
  } else {
    top = Math.max(12, rect.top - box.height - GAP);
  }
  if (top + box.height > vh - 12) top = Math.max(12, vh - box.height - 12);

  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;
}

/**
 * 本文は伸びるが、フッター (辞書ページへの導線) は常に見えるようにする。
 *
 * **戻る道はここで 1 か所だけ足す。** 描き方は 1 件 / 複数 / 読み込み中 / エラーと
 * 4 通りあるので、それぞれに書くと必ずどれかで抜ける（辿った先でだけ戻れない、
 * という壊れ方は画面を見ても分からない）。
 */
function paint(mainHtml, footHtml = "") {
  const node = ensurePop();
  node.innerHTML = `<div class="pop-main">${backBar()}${mainHtml}</div>` + footHtml;
  for (const btn of node.querySelectorAll("[data-pop-close]")) {
    btn.addEventListener("click", hide);
  }
  for (const btn of node.querySelectorAll("[data-pop-back]")) {
    btn.addEventListener("click", back);
  }
  return node;
}

function backBar() {
  if (!trail.length) return "";
  const prev = trail[trail.length - 1];
  return `<p class="pop-back"><button type="button" class="ghost" data-pop-back>`
    + `← ${esc(prev)}</button></p>`;
}

function closeButton() {
  return `<button type="button" class="ghost" data-pop-close>閉じる</button>`;
}

/**
 * ペルソナ（語り手）の顔。**エントリごとに、その辞書のものを出す。**
 *
 * 同じ表記が複数の辞書にあると吹き出しに並ぶので、1 枚を上に置くと
 * どれの語り手なのか分からなくなる（📁 の印と同じ理由で、行ごとに付ける）。
 * 画像が無ければ何も出さない（枠だけ出して欠けて見せない）。
 */
function faceOf(entry) {
  if (!entry.persona_url) return "";
  return `<img class="pop-face" src="${esc(entry.persona_url)}" alt=""`
    + ` title="${esc(entry.path_label || "")} の語り手" loading="lazy">`;
}

function bodyOf(entry) {
  const parts = [];
  if (entry.aliases?.length) {
    parts.push(`<p class="pop-alias">別名: ${esc(entry.aliases.join(" / "))}</p>`);
  }
  if (entry.summary) parts.push(`<p class="pop-summary">${esc(entry.summary)}</p>`);
  if (entry.definition_html) parts.push(`<div class="pop-body">${entry.definition_html}</div>`);
  return parts.join("");
}

function renderLoading(term) {
  paint(`<p class="pop-term">${esc(term)}</p><p class="pop-loading">読み込み中…</p>`);
}

function renderError(term, message) {
  paint(
    `<p class="pop-term">${esc(term)}</p><p class="pop-summary status error">${esc(message)}</p>`,
    `<div class="pop-foot"><span></span>${closeButton()}</div>`
  );
}

function renderMissing(term) {
  paint(
    `<p class="pop-term">${esc(term)}</p>` +
    `<p class="pop-loading">この用語は辞書から削除されたようです。</p>`,
    `<div class="pop-foot"><a href="/glossary?q=${encodeURIComponent(term)}">辞書を検索 →</a>${closeButton()}</div>`
  );
}

/** 1 件だけのとき: 従来どおりの見た目。 */
function renderSingle(entry) {
  const main =
    `<div class="pop-head${entry.persona_url ? " has-face" : ""}">${faceOf(entry)}` +
    `<div><span class="pop-cat">${esc(entry.path_label)}</span>` +
    `<p class="pop-term">${esc(entry.term)}` +
    (entry.reading ? `<span class="pop-reading">${esc(entry.reading)}</span>` : "") +
    `</p></div></div>` +
    bodyOf(entry);
  const foot =
    `<div class="pop-foot">` +
    `<a href="${entry.url}">辞書ページを開く →</a>${closeButton()}</div>`;
  paint(main, foot);
}

/** 複数のとき: カテゴリごとのアコーディオン。先頭だけ開いておく。 */
function renderMultiple(term, entries) {
  const head =
    `<p class="pop-term">${esc(term)}` +
    `<span class="pop-count">${entries.length} 件</span></p>`;
  const items = entries.map((entry, i) => (
    `<details class="pop-item"${i === 0 ? " open" : ""}>` +
    `<summary>${faceOf(entry)}<span class="pop-cat">${esc(entry.path_label)}</span>` +
    (entry.reading ? `<span class="pop-reading">${esc(entry.reading)}</span>` : "") +
    `</summary>` +
    `<div class="pop-item-body">${bodyOf(entry)}` +
    `<p class="pop-item-link"><a href="${entry.url}">辞書ページを開く →</a></p></div>` +
    `</details>`
  )).join("");
  const foot =
    `<div class="pop-foot">` +
    `<a href="/glossary?q=${encodeURIComponent(term)}">一覧で見る →</a>${closeButton()}</div>`;
  paint(head + items, foot);
}

function renderResult(term, data) {
  if (!data.found || !data.entries.length) return renderMissing(term);
  if (data.entries.length === 1) return renderSingle(data.entries[0]);
  return renderMultiple(data.entries[0].term || term, data.entries);
}

/**
 * 吹き出しを出す。``surface`` を渡すと、位置はそのままで**中身だけ**入れ替える
 * （吹き出しの中の語を辿るときに使う）。
 */
async function show(anchor, surface = null) {
  // 新しい語から開き直したときだけ履歴を捨てる。辿るとき (drill / back) は
  // 呼ぶ側がすでに積み下ろししているので触らない
  if (surface === null) trail = [];
  const term = surface ?? (anchor.dataset.gloss || anchor.textContent);
  current = anchor;
  currentSurface = term;
  const token = ++seq;

  if (cache.has(term)) {
    renderResult(term, cache.get(term));
    place(anchor);
    return;
  }
  renderLoading(term);
  place(anchor);
  try {
    const data = await api(`/api/lookup?term=${encodeURIComponent(term)}`);
    if (token !== seq) return;
    cache.set(term, data);
    renderResult(term, data);
  } catch (err) {
    if (token !== seq) return;
    renderError(term, err.message);
  }
  if (current === anchor) place(anchor);
}

//: 辞書が変わった回数。**「変わったか」を知る唯一の合図**として使う ——
//: 覆いを閉じたときに本文を描き直すべきかの判断がこれ（増えていなければ
//: リンクも吹き出しも変わらないので、描き直さない）
let revision = 0;

/** 辞書が更新されたら呼ぶ (吹き出しが古い内容を出さないように)。 */
export function invalidatePopupCache(surface) {
  if (surface) cache.delete(surface);
  else cache.clear();
  revision += 1;
}

/** 辞書が変わった回数。前に取った値と比べて使う。 */
export function dictionaryRevision() {
  return revision;
}

/** ページ全体に 1 回だけ仕掛ける。 */
//: 二重に付けない。ビューアに重ねると、ビューアと用語ページの両方から
//: 呼ばれる（同じ document なので、付けた数だけ吹き出しが開こうとする）
let installed = false;

export function installGlossPopup() {
  if (installed) return;
  installed = true;

  // **吹き出しの中の語はホバーでは開かない**（`inPop`）。開こうとすると、いま
  // ポインタが指している要素ごと差し替わって、元の吹き出しまで閉じる。
  // 中の語は押したときだけ、その場で辿る（`drill`）
  const inPop = (node) => Boolean(pop && node && pop.contains(node));

  document.addEventListener("pointerover", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (!anchor || anchor === current || inPop(anchor)) return;
    if (pinned) return;
    clearTimeout(closeTimer);
    clearTimeout(openTimer);
    openTimer = setTimeout(() => show(anchor), OPEN_DELAY);
  });

  document.addEventListener("pointerout", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (!anchor || pinned || inPop(anchor)) return;
    if (pop && ev.relatedTarget && pop.contains(ev.relatedTarget)) return;
    clearTimeout(openTimer);
    scheduleHide();
  });

  document.addEventListener("focusin", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    // 中の語に焦点が移っただけでは描き替えない（Enter を押せば `drill` で辿る）
    if (anchor && !inPop(anchor)) show(anchor);
  });

  document.addEventListener("click", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (anchor && !inPop(anchor)) {
      // 修飾キーつきクリックは通常のリンクとして扱う (別タブで開く等)
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
      ev.preventDefault();
      clearTimeout(openTimer);
      clearTimeout(closeTimer);
      pinned = true;
      show(anchor);
      return;
    }
    if (pop && !pop.contains(ev.target)) hide();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && pop && !pop.hidden) {
      const anchor = current;
      hide();
      anchor?.focus?.();
    }
  });

  window.addEventListener("scroll", () => {
    if (pop && !pop.hidden && current) place(current);
  }, { passive: true, capture: true });

  window.addEventListener("resize", () => {
    if (pop && !pop.hidden && current) place(current);
  });
}
