// 辞書の 1 語ページ。
import { api, el, esc, paintEntryCount } from "./base.js";
import { installGlossPopup, invalidatePopupCache } from "./popup.js";
import { installSelectionAdd } from "./select-add.js";
import { openEntryEditor } from "./editor.js";

const root = document.getElementById("root");
const countNode = document.getElementById("count");
const slug = decodeURIComponent(location.pathname.replace(/^\/glossary\/?/, "").replace(/\/$/, ""));

installGlossPopup();

/** term / alias -> slug の索引。関連語をリンクにするために使う。 */
let index = new Map();
/** 表示中のエントリ。 */
let current = null;

// 辞書ページの本文でも、知らない語を選択してそのまま登録できるようにする
const selection = installSelectionAdd({
  root,
  source: () => (current ? `辞書: ${current.term}` : "辞書"),
  onSaved: async () => {
    invalidatePopupCache();
    await Promise.all([loadIndex(), reload()]);
    paintEntryCount(countNode);
  },
});

async function loadIndex() {
  try {
    const entries = await api("/api/entries");
    index = new Map();
    for (const e of entries) {
      for (const s of [e.term, ...(e.aliases || [])]) index.set(s.toLowerCase(), e.slug);
    }
  } catch { /* 索引が無くてもページは出す */ }
}

function chip(text, href) {
  return el("a", { class: "chip", href, text });
}

function section(title, children) {
  return el("section", { class: "entry-section" }, [el("h2", { text: title }), ...[].concat(children)]);
}

function render(entry) {
  current = entry;
  const head = el("div", { class: "entry-head" }, [
    el("div", { class: "crumb", html:
      `<a href="/glossary">辞書</a> / ` +
      `<a href="/glossary?category=${encodeURIComponent(entry.category)}">${esc(entry.category)}</a>` +
      (entry.subcategory ? ` / ${esc(entry.subcategory)}` : "")
    }),
    el("h1", { html: esc(entry.term) + (entry.reading ? `<span class="reading">${esc(entry.reading)}</span>` : "") }),
  ]);
  if (entry.aliases?.length) {
    head.append(el("p", { class: "aliases", text: `別名: ${entry.aliases.join(" / ")}` }));
  }
  if (entry.summary) head.append(el("p", { class: "summary", text: entry.summary }));
  if (entry.tags?.length) {
    head.append(el("div", { class: "chips" }, entry.tags.map((t) => chip(`#${t}`, `/glossary?q=${encodeURIComponent(t)}`))));
  }

  const parts = [head];

  if (entry.definition_html) {
    parts.push(el("article", { class: "doc", html: entry.definition_html }));
  } else {
    parts.push(el("p", { class: "empty", text: "本文が未記入です。" }));
  }

  if (entry.examples_html?.length) {
    parts.push(section("使用例", el("div", { class: "doc", html: entry.examples_html.join("") })));
  }

  if (entry.related?.length) {
    parts.push(section("関連語", el("div", { class: "chips" }, entry.related.map((r) => {
      const target = index.get(r.toLowerCase());
      return chip(r, target ? `/glossary/${encodeURIComponent(target)}` : `/glossary?q=${encodeURIComponent(r)}`);
    }))));
  }

  parts.push(el("div", { class: "toolbar entry-actions" }, [
    el("button", { type: "button", text: "編集", onclick: () => edit(entry) }),
    el("button", { type: "button", class: "danger", text: "削除", onclick: () => remove(entry) }),
    el("a", { class: "btn", href: "/glossary", text: "一覧へ戻る" }),
  ]));

  const meta = [`slug: ${entry.slug}`];
  if (entry.source) meta.push(`出典: ${entry.source}`);
  meta.push(`作成 ${entry.created_at}`, `更新 ${entry.updated_at}`);
  parts.push(el("p", { class: "entry-meta", text: meta.join("  ·  ") }));

  root.replaceChildren(...parts);
  document.title = `${entry.term} — GlossPop`;
}

/** サーバから引き直して描き直す (本文の自動リンクを最新にするため)。 */
async function reload() {
  const target = current?.slug || slug;
  render(await api(`/api/entries/${encodeURIComponent(target)}`));
}

async function edit(entry) {
  const saved = await openEntryEditor({ slug: entry.slug, entry });
  if (!saved) return;
  invalidatePopupCache();
  selection.hide();
  if (saved.slug !== entry.slug) {
    location.href = `/glossary/${encodeURIComponent(saved.slug)}`;
    return;
  }
  await loadIndex();
  render(saved);
  paintEntryCount(countNode);
}

async function remove(entry) {
  if (!confirm(`「${entry.term}」を辞書から削除します。よろしいですか？`)) return;
  try {
    await api(`/api/entries/${encodeURIComponent(entry.slug)}`, { method: "DELETE" });
    invalidatePopupCache(entry.slug);
    location.href = "/glossary";
  } catch (err) {
    alert(`削除できません: ${err.message}`);
  }
}

async function main() {
  paintEntryCount(countNode);
  await loadIndex();
  try {
    await reload();
  } catch (err) {
    root.replaceChildren(
      el("h1", { text: "見つかりません" }),
      el("p", { class: "status error", text: err.message }),
      el("p", {}, [el("a", { class: "btn", href: "/glossary", text: "辞書一覧へ" })]),
    );
  }
}

main();
