// テキスト選択 → 「＋ 辞書に登録」ボタン → 登録ダイアログ。
// ビューアと辞書ページの両方で使う。
import { api } from "./base.js";
import { openEntryEditor } from "./editor.js";

const MAX_TERM_LEN = 100;
const CONTEXT_LEN = 1400;
const BLOCK_SELECTOR = "p,li,td,th,h1,h2,h3,h4,h5,h6,blockquote,pre,dd,dt,figcaption";

/** 選択範囲の前後を含めた文脈テキストを取り出す (AI 下書きの精度用)。 */
function selectionContext(range) {
  const start = range.commonAncestorContainer;
  const startEl = start.nodeType === Node.ELEMENT_NODE ? start : start.parentElement;
  const block = startEl?.closest(BLOCK_SELECTOR) || startEl;
  if (!block) return "";
  const chunks = [block.textContent || ""];
  let prev = block.previousElementSibling;
  let next = block.nextElementSibling;
  while (chunks.join("\n").length < CONTEXT_LEN && (prev || next)) {
    if (prev) {
      chunks.unshift(prev.textContent || "");
      prev = prev.previousElementSibling;
    }
    if (next) {
      chunks.push(next.textContent || "");
      next = next.nextElementSibling;
    }
  }
  return chunks.join("\n").replace(/\n{3,}/g, "\n\n").trim().slice(0, CONTEXT_LEN);
}

async function lookup(term) {
  try {
    const res = await api(`/api/lookup?term=${encodeURIComponent(term)}`);
    return res.found ? res.entry : null;
  } catch {
    return null; // 引けなくても新規登録として続行する
  }
}

/**
 * @param {object} o
 * @param {Element} o.root        この要素の中の選択だけを拾う
 * @param {() => string} [o.source] 出典として記録する文字列を返す
 * @param {(entry: object) => void} [o.onSaved] 保存後に呼ばれる (再描画用)
 */
export function installSelectionAdd({ root, source = () => "", onSaved = () => {} }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "sel-add primary";
  button.textContent = "＋ 辞書に登録";
  button.hidden = true;
  document.body.append(button);

  let pending = null;

  function hide() {
    pending = null;
    button.hidden = true;
  }

  function refresh() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return hide();
    const range = sel.getRangeAt(0);
    if (!root || !root.contains(range.commonAncestorContainer)) return hide();

    const term = sel.toString().replace(/\s+/g, " ").trim();
    if (!term || term.length > MAX_TERM_LEN) return hide();

    pending = { term, context: selectionContext(range) };

    const rect = range.getBoundingClientRect();
    if (!rect.width && !rect.height) return hide();
    button.hidden = false;
    const width = button.offsetWidth || 140;
    const height = button.offsetHeight || 32;
    const left = Math.min(rect.left, window.innerWidth - width - 12);
    let top = rect.bottom + 8;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 8);
    button.style.left = `${Math.round(Math.max(8, left))}px`;
    button.style.top = `${Math.round(top)}px`;
  }

  document.addEventListener("mouseup", () => setTimeout(refresh, 0));
  document.addEventListener("keyup", (ev) => {
    if (ev.shiftKey || ev.key === "Shift") setTimeout(refresh, 0);
  });
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) hide();
  });
  window.addEventListener("scroll", () => {
    if (!button.hidden) refresh();
  }, { passive: true, capture: true });
  window.addEventListener("resize", () => {
    if (!button.hidden) refresh();
  });

  // mousedown を止めて、クリック時点でも選択を保持しておく
  button.addEventListener("mousedown", (ev) => ev.preventDefault());
  button.addEventListener("click", async () => {
    if (!pending) return;
    const { term, context } = pending;
    hide();
    window.getSelection()?.removeAllRanges();

    // すでに登録済みの語なら 409 で弾くのではなく編集モードで開く
    const existing = await lookup(term);
    const saved = await openEntryEditor(
      existing
        ? { slug: existing.slug, entry: existing, context }
        : { term, context, source: source() }
    );
    if (saved) onSaved(saved);
  });

  return { refresh, hide, button };
}
