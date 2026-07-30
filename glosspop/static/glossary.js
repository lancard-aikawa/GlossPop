// 辞書一覧: カテゴリ / サブカテゴリでグルーピングして表示。
import { api, el, esc, paintEntryCount } from "./base.js";
import { openEntryEditor } from "./editor.js";

const $ = (id) => document.getElementById(id);
const list = $("list");
const qInput = $("q");
const catFilter = $("catFilter");

let timer = null;

function groupEntries(entries) {
  const cats = new Map();
  for (const e of entries) {
    if (!cats.has(e.category)) cats.set(e.category, new Map());
    const subs = cats.get(e.category);
    const key = e.subcategory || "";
    if (!subs.has(key)) subs.set(key, []);
    subs.get(key).push(e);
  }
  return cats;
}

function card(e) {
  return el("a", { class: "card", href: `/glossary/${encodeURIComponent(e.slug)}` }, [
    el("div", { class: "t", html: esc(e.term) + (e.reading ? `<span class="r">${esc(e.reading)}</span>` : "") }),
    el("div", { class: "s", text: e.summary || (e.aliases?.length ? `別名: ${e.aliases.join(" / ")}` : "（要約なし）") }),
  ]);
}

function paint(entries) {
  if (!entries.length) {
    list.replaceChildren(
      el("p", { class: "empty", text: qInput.value.trim() ? "該当する用語がありません" : "まだ用語が登録されていません" })
    );
    return;
  }
  const groups = groupEntries(entries);
  const nodes = [];
  for (const [category, subs] of [...groups].sort((a, b) => a[0].localeCompare(b[0], "ja"))) {
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
    $("lede").textContent = `${entries.length} 語`;
  } catch (err) {
    list.replaceChildren(el("p", { class: "status error", text: err.message }));
  } finally {
    list.removeAttribute("aria-busy");
  }
}

async function loadCategories() {
  try {
    const tree = await api("/api/categories");
    const current = catFilter.value;
    catFilter.replaceChildren(
      el("option", { value: "", text: "すべてのカテゴリ" }),
      ...tree.map((n) => el("option", { value: n.category, text: `${n.category} (${n.count})` })),
    );
    catFilter.value = current;
  } catch { /* カテゴリが取れなくても一覧は出す */ }
}

qInput.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(reload, 180);
});
catFilter.addEventListener("change", reload);

$("add").addEventListener("click", async () => {
  const saved = await openEntryEditor({});
  if (saved) {
    await Promise.all([loadCategories(), reload(), paintEntryCount($("count"))]);
  }
});

// ?q= / ?category= を URL から復元
const initial = new URLSearchParams(location.search);
qInput.value = initial.get("q") || "";

paintEntryCount($("count"));
loadCategories().then(() => {
  const cat = initial.get("category");
  if (cat) catFilter.value = cat;
  reload();
});
