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
let current = null; // 表示中のアンカー
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
  // アコーディオンを開閉しても閉じないよう、内側のクリックは外に伝えない
  pop.addEventListener("click", (ev) => ev.stopPropagation());
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
  if (pop) pop.hidden = true;
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

/** 本文は伸びるが、フッター (辞書ページへの導線) は常に見えるようにする。 */
function paint(mainHtml, footHtml = "") {
  const node = ensurePop();
  node.innerHTML = `<div class="pop-main">${mainHtml}</div>` + footHtml;
  for (const btn of node.querySelectorAll("[data-pop-close]")) {
    btn.addEventListener("click", hide);
  }
  return node;
}

function closeButton() {
  return `<button type="button" class="ghost" data-pop-close>閉じる</button>`;
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
    `<span class="pop-cat">${esc(entry.path_label)}</span>` +
    `<p class="pop-term">${esc(entry.term)}` +
    (entry.reading ? `<span class="pop-reading">${esc(entry.reading)}</span>` : "") +
    `</p>` +
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
    `<summary><span class="pop-cat">${esc(entry.path_label)}</span>` +
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

async function show(anchor) {
  const surface = anchor.dataset.gloss || anchor.textContent;
  current = anchor;
  const token = ++seq;

  if (cache.has(surface)) {
    renderResult(surface, cache.get(surface));
    place(anchor);
    return;
  }
  renderLoading(surface);
  place(anchor);
  try {
    const data = await api(`/api/lookup?term=${encodeURIComponent(surface)}`);
    if (token !== seq) return;
    cache.set(surface, data);
    renderResult(surface, data);
  } catch (err) {
    if (token !== seq) return;
    renderError(surface, err.message);
  }
  if (current === anchor) place(anchor);
}

/** 辞書が更新されたら呼ぶ (吹き出しが古い内容を出さないように)。 */
export function invalidatePopupCache(surface) {
  if (surface) cache.delete(surface);
  else cache.clear();
}

/** ページ全体に 1 回だけ仕掛ける。 */
export function installGlossPopup() {
  document.addEventListener("pointerover", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (!anchor || anchor === current) return;
    if (pinned) return;
    clearTimeout(closeTimer);
    clearTimeout(openTimer);
    openTimer = setTimeout(() => show(anchor), OPEN_DELAY);
  });

  document.addEventListener("pointerout", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (!anchor || pinned) return;
    if (pop && ev.relatedTarget && pop.contains(ev.relatedTarget)) return;
    clearTimeout(openTimer);
    scheduleHide();
  });

  document.addEventListener("focusin", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (anchor) show(anchor);
  });

  document.addEventListener("click", (ev) => {
    const anchor = ev.target.closest?.("a.gloss-link");
    if (anchor) {
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
