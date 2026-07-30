// 辞書一覧: カテゴリマスターの順にグルーピングして表示。
import { api, el, esc, paintEntryCount, setStatus } from "./base.js";
import { openEntryEditor } from "./editor.js";

const $ = (id) => document.getElementById(id);
const list = $("list");
const qInput = $("q");
const catFilter = $("catFilter");
const catDialog = $("catDialog");

let tree = [];
let timer = null;

function card(e) {
  return el("a", { class: "card", href: e.url }, [
    el("div", { class: "t", html: esc(e.term) + (e.reading ? `<span class="r">${esc(e.reading)}</span>` : "") }),
    el("div", { class: "s", text: e.summary || (e.aliases?.length ? `別名: ${e.aliases.join(" / ")}` : "（要約なし）") }),
  ]);
}

function paint(entries) {
  const filtering = Boolean(qInput.value.trim() || catFilter.value);
  const byCategory = new Map();
  for (const e of entries) {
    if (!byCategory.has(e.category)) byCategory.set(e.category, new Map());
    const subs = byCategory.get(e.category);
    const key = e.subcategory || "";
    if (!subs.has(key)) subs.set(key, []);
    subs.get(key).push(e);
  }

  // マスターの順で並べ、マスターに無いカテゴリは後ろに付ける
  const order = tree.map((n) => n.category);
  for (const name of byCategory.keys()) if (!order.includes(name)) order.push(name);

  const nodes = [];
  for (const category of order) {
    const subs = byCategory.get(category);
    if (!subs) {
      // 絞り込み中に空カテゴリを見せても邪魔なので、素の一覧のときだけ出す
      if (filtering) continue;
      const meta = tree.find((n) => n.category === category);
      nodes.push(el("section", { class: "cat-group empty-cat" }, [
        el("h2", {}, [
          el("span", { text: category }),
          el("span", { class: "count", text: "0 語" }),
          meta?.description ? el("span", { class: "count", text: meta.description }) : null,
        ]),
      ]));
      continue;
    }
    const total = [...subs.values()].reduce((n, arr) => n + arr.length, 0);
    const group = el("section", { class: "cat-group" }, [
      el("h2", {}, [
        el("span", { text: category }),
        el("span", { class: "count", text: `${total} 語` }),
      ]),
    ]);
    const subKeys = [...subs.keys()].sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b, "ja")));
    for (const sub of subKeys) {
      const items = subs.get(sub).sort((a, b) => (a.reading || a.term).localeCompare(b.reading || b.term, "ja"));
      const wrap = el("div", { class: "sub-group" });
      if (sub) wrap.append(el("h3", { text: sub }));
      wrap.append(el("div", { class: "cards" }, items.map(card)));
      group.append(wrap);
    }
    nodes.push(group);
  }

  if (!nodes.length) {
    list.replaceChildren(
      el("p", { class: "empty", text: filtering ? "該当する用語がありません" : "まだ用語が登録されていません" })
    );
    return;
  }
  list.replaceChildren(...nodes);
}

async function reload() {
  const params = new URLSearchParams();
  if (qInput.value.trim()) params.set("q", qInput.value.trim());
  if (catFilter.value) params.set("category", catFilter.value);
  const qs = params.toString();
  list.setAttribute("aria-busy", "true");
  try {
    const entries = await api(`/api/entries${qs ? "?" + qs : ""}`);
    paint(entries);
    const dup = countDuplicates(entries);
    $("lede").textContent =
      `${entries.length} 語 / ${tree.length} カテゴリ` +
      (dup ? ` — ${dup} 語がカテゴリ違いで重複しています` : "");
  } catch (err) {
    list.replaceChildren(el("p", { class: "status error", text: err.message }));
  } finally {
    list.removeAttribute("aria-busy");
  }
}

function countDuplicates(entries) {
  const seen = new Map();
  for (const e of entries) seen.set(e.term, (seen.get(e.term) || 0) + 1);
  return [...seen.values()].filter((n) => n > 1).length;
}

async function loadCategories() {
  try {
    tree = await api("/api/categories");
  } catch {
    tree = [];
  }
  const current = catFilter.value;
  catFilter.replaceChildren(
    el("option", { value: "", text: "すべてのカテゴリ" }),
    ...tree.map((n) => el("option", { value: n.category, text: `${n.category} (${n.count})` })),
  );
  catFilter.value = current;
}

// ------------------------------------------------------------ カテゴリ管理

function categoryRow(node) {
  const nameNode = el("div", { class: "cat-row-name", text: node.category });
  const meta = el("div", { class: "cat-row-meta", text:
    `${node.count} 語` +
    (node.subcategories.filter((s) => s.name).length
      ? ` · ${node.subcategories.filter((s) => s.name).map((s) => s.name).join(" / ")}`
      : "") +
    (node.description ? ` · ${node.description}` : "")
  });
  const row = el("div", { class: "cat-row" }, [
    el("div", { class: "cat-row-main" }, [nameNode, meta]),
    el("button", { type: "button", text: "名前を変更", onclick: () => startRename(row, node) }),
    el("button", {
      type: "button", class: "danger", text: "削除",
      disabled: node.count > 0,
      title: node.count > 0 ? "用語が入っているカテゴリは削除できません" : "",
      onclick: () => removeCategory(node),
    }),
  ]);
  return row;
}

/** 行をその場で入力欄に差し替える (prompt を使わない)。 */
function startRename(row, node) {
  const input = el("input", { type: "text", value: node.category, "aria-label": "新しいカテゴリ名" });
  const status = el("span", { class: "status" });
  const save = el("button", {
    type: "button", class: "primary", text: "保存",
    onclick: async () => {
      const next = input.value.trim();
      if (!next || next === node.category) return row.replaceWith(categoryRow(node));
      save.disabled = true;
      setStatus(status, "変更中", "busy");
      try {
        await api(`/api/categories/${encodeURIComponent(node.category)}`, {
          method: "PUT",
          body: { name: next, subcategories: node.subcategories.map((s) => s.name).filter(Boolean) },
        });
        await refreshAll();
      } catch (err) {
        setStatus(status, err.message, "error");
        save.disabled = false;
      }
    },
  });
  const editing = el("div", { class: "cat-row" }, [
    el("div", { class: "cat-row-main" }, [input, status]),
    save,
    el("button", { type: "button", text: "やめる", onclick: () => editing.replaceWith(categoryRow(node)) }),
  ]);
  row.replaceWith(editing);
  input.focus();
  input.select();
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); save.click(); }
    if (ev.key === "Escape") { ev.preventDefault(); editing.replaceWith(categoryRow(node)); }
  });
}

function paintCategoryManager() {
  const body = catDialog.querySelector("[data-ref=catBody]");
  if (!tree.length) {
    body.replaceChildren(el("p", { class: "empty", text: "カテゴリがまだありません" }));
    return;
  }
  body.replaceChildren(...tree.map(categoryRow));
}

async function refreshAll() {
  await loadCategories();
  await reload();
  paintCategoryManager();
  paintEntryCount($("count"));
}

async function removeCategory(node) {
  if (!confirm(`カテゴリ「${node.category}」を削除します。よろしいですか？`)) return;
  try {
    await api(`/api/categories/${encodeURIComponent(node.category)}`, { method: "DELETE" });
    await refreshAll();
  } catch (err) {
    alert(`削除できません: ${err.message}`);
  }
}

async function addCategory() {
  const input = catDialog.querySelector("[data-ref=catNew]");
  const status = catDialog.querySelector("[data-ref=catStatus]");
  const name = input.value.trim();
  if (!name) {
    input.focus();
    return;
  }
  setStatus(status, "登録中", "busy");
  try {
    await api("/api/categories", { method: "POST", body: { name } });
    input.value = "";
    setStatus(status, `「${name}」を登録しました`);
    await refreshAll();
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

// ------------------------------------------------------------------- 起動

qInput.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(reload, 180);
});
catFilter.addEventListener("change", reload);

$("add").addEventListener("click", async () => {
  const saved = await openEntryEditor({});
  if (saved) await refreshAll();
});

$("manageCats").addEventListener("click", () => {
  paintCategoryManager();
  catDialog.showModal();
});
catDialog.querySelector("[data-ref=catAdd]").addEventListener("click", addCategory);
catDialog.querySelector("[data-ref=catNew]").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    addCategory();
  }
});
catDialog.querySelector("[data-ref=catClose]").addEventListener("click", () => catDialog.close());

const initial = new URLSearchParams(location.search);
qInput.value = initial.get("q") || "";

paintEntryCount($("count"));
loadCategories().then(() => {
  const cat = initial.get("category");
  if (cat) catFilter.value = cat;
  reload();
});
