// 辞書リンクの吹き出し。ホバー / フォーカスで表示、クリックで固定表示。
import { api, esc } from "./base.js";

const OPEN_DELAY = 130;
const CLOSE_DELAY = 220;
const GAP = 8;

const cache = new Map();
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
  node.querySelector("[data-pop-close]")?.addEventListener("click", hide);
  return node;
}

function renderLoading(term) {
  paint(`<p class="pop-term">${esc(term)}</p><p class="pop-loading">読み込み中…</p>`);
}

function renderError(term, message) {
  paint(`<p class="pop-term">${esc(term)}</p><p class="pop-summary status error">${esc(message)}</p>`);
}

function renderEntry(entry) {
  const main = [
    `<span class="pop-cat">${esc(entry.path_label)}</span>`,
    `<p class="pop-term">${esc(entry.term)}` +
      (entry.reading ? `<span class="pop-reading">${esc(entry.reading)}</span>` : "") +
      `</p>`,
  ];
  if (entry.aliases?.length) {
    main.push(`<p class="pop-alias">別名: ${esc(entry.aliases.join(" / "))}</p>`);
  }
  if (entry.summary) main.push(`<p class="pop-summary">${esc(entry.summary)}</p>`);
  if (entry.definition_html) main.push(`<div class="pop-body">${entry.definition_html}</div>`);

  const foot =
    `<div class="pop-foot">` +
    `<a href="/glossary/${encodeURIComponent(entry.slug)}">辞書ページを開く →</a>` +
    `<button type="button" class="ghost" data-pop-close>閉じる</button>` +
    `</div>`;
  paint(main.join(""), foot);
}

async function show(anchor) {
  const slug = anchor.dataset.gloss;
  const term = anchor.dataset.term || anchor.textContent;
  current = anchor;
  const token = ++seq;

  if (cache.has(slug)) {
    renderEntry(cache.get(slug));
    place(anchor);
    return;
  }
  renderLoading(term);
  place(anchor);
  try {
    const entry = await api(`/api/entries/${encodeURIComponent(slug)}`);
    if (token !== seq) return;
    cache.set(slug, entry);
    renderEntry(entry);
  } catch (err) {
    if (token !== seq) return;
    renderError(term, err.message);
  }
  if (current === anchor) place(anchor);
}

/** 辞書が更新されたら呼ぶ (吹き出しが古い内容を出さないように)。 */
export function invalidatePopupCache(slug) {
  if (slug) cache.delete(slug);
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
