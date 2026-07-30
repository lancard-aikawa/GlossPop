// 辞書の 1 語ページ。URL は /glossary/<カテゴリ>/<slug>
import { api, el, esc, paintEntryCount, setStatus, sourceNode } from "./base.js";
import { installGlossPopup, invalidatePopupCache } from "./popup.js";
import { installSelectionAdd } from "./select-add.js";
import { openEntryEditor, encodePath } from "./editor.js";

const root = document.getElementById("root");
const countNode = document.getElementById("count");
const initialRef = decodeURIComponent(
  location.pathname.replace(/^\/glossary\/?/, "").replace(/\/$/, "")
);

installGlossPopup();

/** 表記 -> [{ref, path_label}] の索引。関連語をリンクにするために使う。 */
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
      for (const s of [e.term, ...(e.aliases || [])]) {
        const key = s.toLowerCase();
        if (!index.has(key)) index.set(key, []);
        index.get(key).push(e);
      }
    }
  } catch { /* 索引が無くてもページは出す */ }
}

function chip(text, href) {
  return el("a", { class: "chip", href, text });
}

function section(title, children) {
  return el("section", { class: "entry-section" }, [el("h2", { text: title }), ...[].concat(children)]);
}

function relatedChip(name) {
  const hits = index.get(name.toLowerCase()) || [];
  if (hits.length === 1) return chip(name, hits[0].url);
  // 同名が複数カテゴリにある / 未登録 → 検索に飛ばす
  const label = hits.length > 1 ? `${name} (${hits.length})` : name;
  return chip(label, `/glossary?q=${encodeURIComponent(name)}`);
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

  // 同じ表記が別カテゴリにもあるなら案内する
  const siblings = (index.get(entry.term.toLowerCase()) || []).filter((e) => e.ref !== entry.ref);
  if (siblings.length) {
    parts.push(el("p", { class: "notice" }, [
      el("span", { text: `「${entry.term}」は他のカテゴリにもあります: ` }),
      ...siblings.flatMap((e, i) => [
        i ? el("span", { text: "、" }) : null,
        el("a", { href: e.url, text: e.path_label }),
      ].filter(Boolean)),
    ]));
  }

  if (entry.definition_html) {
    parts.push(el("article", { class: "doc", html: entry.definition_html }));
  } else {
    parts.push(el("p", { class: "empty", text: "本文が未記入です。" }));
  }

  if (entry.examples_html?.length) {
    parts.push(section("使用例", el("div", { class: "doc", html: entry.examples_html.join("") })));
  }

  if (entry.related?.length) {
    parts.push(section("関連語", el("div", { class: "chips" }, entry.related.map(relatedChip))));
  }

  const movePanel = el("div", { class: "move-panel", hidden: true });
  parts.push(el("div", { class: "toolbar entry-actions" }, [
    el("button", { type: "button", text: "編集", onclick: () => edit(entry) }),
    el("button", {
      type: "button",
      text: "カテゴリを移動",
      onclick: () => toggleMovePanel(entry, movePanel),
    }),
    el("button", { type: "button", class: "danger", text: "削除", onclick: () => remove(entry) }),
    el("a", { class: "btn", href: "/glossary", text: "一覧へ戻る" }),
  ]));
  parts.push(movePanel);

  const meta = el("p", { class: "entry-meta" });
  const bits = [
    el("span", { text: `保存先: ${entry.path}`, title: entry.path }),
    sourceNode(entry.source),
    el("span", { text: `作成 ${entry.created_at}` }),
    el("span", { text: `更新 ${entry.updated_at}` }),
  ].filter(Boolean);
  bits.forEach((node, i) => {
    if (i) meta.append(el("span", { class: "sep", text: "·" }));
    meta.append(node);
  });
  parts.push(meta);

  root.replaceChildren(...parts);
  document.title = `${entry.term} — GlossPop`;
}

/** サーバから引き直して描き直す (本文の自動リンクを最新にするため)。 */
async function reload(ref) {
  const target = ref || current?.ref || initialRef;
  const entry = await api(`/api/entries/${encodePath(target)}`);
  render(entry);
  return entry;
}

function goTo(ref) {
  location.href = `/glossary/${encodePath(ref)}`;
}

async function edit(entry) {
  const saved = await openEntryEditor({ ref: entry.ref, entry });
  if (!saved) return;
  invalidatePopupCache();
  selection.hide();
  if (saved.ref !== entry.ref) return goTo(saved.ref); // カテゴリ移動 / slug 変更
  await loadIndex();
  render(saved);
  paintEntryCount(countNode);
}

const NEW_CATEGORY = "/new";  // "/" はカテゴリ名で禁止なので実名と衝突しない番兵

async function toggleMovePanel(entry, panel) {
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  const tree = await api("/api/categories").catch(() => []);
  const others = tree.map((n) => n.category).filter((n) => n !== entry.category);

  const select = el("select", { class: "auto-width", "aria-label": "移動先カテゴリ" }, [
    ...others.map((n) => el("option", { value: n, text: n })),
    el("option", { value: NEW_CATEGORY, text: "＋ 新しいカテゴリ…" }),
  ]);
  const input = el("input", {
    type: "text",
    placeholder: "新しいカテゴリ名",
    "aria-label": "新しいカテゴリ名",
    hidden: others.length > 0,
  });
  if (!others.length) select.value = NEW_CATEGORY;
  select.addEventListener("change", () => {
    input.hidden = select.value !== NEW_CATEGORY;
    if (!input.hidden) input.focus();
  });

  const status = el("span", { class: "status" });
  const go = el("button", {
    type: "button",
    class: "primary",
    text: "移動",
    onclick: async () => {
      const target = (select.value === NEW_CATEGORY ? input.value : select.value).trim();
      if (!target) {
        setStatus(status, "移動先を選んでください", "error");
        return;
      }
      go.disabled = true;
      setStatus(status, "移動中", "busy");
      try {
        const moved = await api(`/api/move/${encodePath(entry.ref)}`, {
          method: "POST",
          body: { category: target },
        });
        invalidatePopupCache();
        goTo(moved.ref);
      } catch (err) {
        setStatus(status, err.message, "error");
        go.disabled = false;
      }
    },
  });

  panel.replaceChildren(
    el("span", { class: "hint", text: `現在: ${entry.category} →` }),
    select,
    input,
    go,
    el("button", { type: "button", text: "やめる", onclick: () => { panel.hidden = true; } }),
    status,
  );
  panel.hidden = false;
  select.focus();
}

async function remove(entry) {
  if (!confirm(`「${entry.term}」（${entry.path_label}）を辞書から削除します。よろしいですか？`)) return;
  try {
    await api(`/api/entries/${encodePath(entry.ref)}`, { method: "DELETE" });
    invalidatePopupCache();
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
      el("p", { class: "hint", text: `参照: ${initialRef}` }),
      el("p", {}, [el("a", { class: "btn", href: "/glossary", text: "辞書一覧へ" })]),
    );
  }
}

main();
