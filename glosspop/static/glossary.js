// 辞書一覧: カテゴリマスターの順にグルーピングして表示。
import { api, el, esc, paintEntryCount, setStatus } from "./base.js";
import { openEntryEditor } from "./editor.js";
import { installSelectionAdd } from "./select-add.js";
import { invalidatePopupCache } from "./popup.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない）
const TEMPLATE = `
<h1>用語辞書</h1>
<p class="lede" id="lede"></p>

<div class="toolbar">
  <input type="search" id="q" placeholder="用語・別名・本文を検索" autocomplete="off"
         title="用語・別名・本文を検索" aria-label="用語・別名・本文を検索">
  <select id="catFilter" class="auto-width" title="カテゴリで絞り込む" aria-label="カテゴリで絞り込む">
    <option value="">すべてのカテゴリ</option>
  </select>
  <select id="tagFilter" class="auto-width" title="タグで絞り込む" aria-label="タグで絞り込む">
    <option value="">すべてのタグ</option>
  </select>
  <span class="spacer"></span>
  <button type="button" id="manageCats">カテゴリ管理</button>
  <button type="button" class="primary" id="add">＋ 新規登録</button>
</div>

<div id="list"></div>

<dialog class="sheet" id="catDialog">
  <div class="cat-manager">
    <header>
      <h2>カテゴリ管理</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="catClose" aria-label="閉じる">✕</button>
    </header>
    <div class="body" data-ref="catBody"></div>
    <footer>
      <select data-ref="catScope" title="どちらの辞書に作るか" aria-label="どちらの辞書に作るか" hidden>
        <option value="global">全体の辞書</option>
        <option value="local">📁 このフォルダの辞書</option>
      </select>
      <input type="text" data-ref="catNew" class="auto-width" placeholder="新しいカテゴリ名"
             title="新しいカテゴリ名" aria-label="新しいカテゴリ名" autocomplete="off">
      <button type="button" data-ref="catAdd">＋ 追加</button>
      <span class="status" data-ref="catStatus"></span>
      <span class="spacer"></span>
      <span class="hint">用語 0 件のカテゴリも登録できます。並びは ↑ ↓ で変えられます。削除できるのは空のカテゴリだけです。</span>
    </footer>
  </div>
</dialog>
`;

//: 描く先。`mount()` で埋める（`location` は読まない —— 重ねたときに
//: 「覆いが出しているもの」と食い違う）
let host = null;
let list, qInput, catFilter, tagFilter, catDialog;
const $ = (id) => host.querySelector(`#${id}`);

let tree = [];
let timer = null;
//: 最初の読み込み。**開くのが速すぎたダイアログがこれを待って描き直す**
let ready = null;

/** 選択したまま指を離したか。カードの上でのドラッグ選択と、リンクの遷移を分ける。 */
function hasSelection() {
  const sel = window.getSelection();
  return Boolean(sel && !sel.isCollapsed && sel.toString().trim());
}

/**
 * 1 枚のカード。**カード全体を `<a>` にしない。**
 *
 * `<a>` の中ではドラッグが「リンクを掴む」になり、要約の語を選べない
 * （`draggable="false"` を付けても Chrome は選択させてくれない。実際に試した）。
 * 一覧にだけ選択 → 登録の口が無かった理由がこれ。
 *
 * 代わりに、**見出しだけを本物のリンク**にしてカード全体はクリックで飛ばす。
 * こうすると見出しの中クリック・Ctrl クリック（別タブ）はそのまま効き、
 * 要約の上ではふつうに文字を選べる。
 */
function card(e) {
  const node = el("div", { class: "card" }, [
    el("a", {
      class: "t", href: e.url,
      html: esc(e.term) + (e.reading ? `<span class="r">${esc(e.reading)}</span>` : ""),
    }),
    el("div", { class: "s", text: e.summary || (e.aliases?.length ? `別名: ${e.aliases.join(" / ")}` : "（要約なし）") }),
  ]);
  node.addEventListener("click", (ev) => {
    if (ev.target.closest("a")) return;      // 見出しのリンクはブラウザに任せる
    if (hasSelection()) return;              // 選んでいる最中は飛ばさない
    location.href = e.url;
  });
  return node;
}

/**
 * グルーピングの鍵。**カテゴリ名だけでは割れない。**
 *
 * 同じ名前のカテゴリが全体とフォルダの両方にありうるので、名前で束ねると
 * 2 つの辞書の用語が 1 つの見出しに混ざり、マスターの順で並べるときに
 * 同じ見出しが 2 回出る。区切りは `<>`（カテゴリ名でも slug でも弾かれる）。
 */
const groupKey = (scope, category) => `${scope}<>${category}`;

function paint(entries) {
  const filtering = Boolean(qInput.value.trim() || catFilter.value || tagFilter.value);
  const byCategory = new Map();
  for (const e of entries) {
    const gk = groupKey(e.scope, e.category);
    if (!byCategory.has(gk)) byCategory.set(gk, new Map());
    const subs = byCategory.get(gk);
    const key = e.subcategory || "";
    if (!subs.has(key)) subs.set(key, []);
    subs.get(key).push(e);
  }

  // マスターの順で並べ、マスターに無いカテゴリは後ろに付ける
  const order = tree.map((n) => groupKey(n.scope, n.category));
  for (const gk of byCategory.keys()) if (!order.includes(gk)) order.push(gk);

  const nodes = [];
  for (const gk of order) {
    const subs = byCategory.get(gk);
    const meta = tree.find((n) => groupKey(n.scope, n.category) === gk);
    const category = meta ? meta.category : gk.slice(gk.indexOf("<>") + 2);
    const heading = meta ? categoryLabel(meta) : category;
    if (!subs) {
      // 絞り込み中に空カテゴリを見せても邪魔なので、素の一覧のときだけ出す
      if (filtering) continue;
      nodes.push(el("section", { class: "cat-group empty-cat" }, [
        el("h2", {}, [
          el("span", { text: heading }),
          el("span", { class: "count", text: "0 語" }),
          meta?.description ? el("span", { class: "count", text: meta.description }) : null,
        ]),
      ]));
      continue;
    }
    const total = [...subs.values()].reduce((n, arr) => n + arr.length, 0);
    const group = el("section", { class: "cat-group" }, [
      el("h2", {}, [
        el("span", { text: heading }),
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
  const picked = categorySelection();
  if (picked.category) {
    params.set("category", picked.category);
    params.set("scope", picked.scope);
  }
  if (tagFilter.value) params.set("tag", tagFilter.value);
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
  // **値にスコープを持たせる。** 同じ名前のカテゴリが全体とフォルダの両方にあると、
  // 名前だけでは見分けが付かない。区切りは "/"（カテゴリ名では禁止）で 1 回だけ割る
  catFilter.replaceChildren(
    el("option", { value: "", text: "すべてのカテゴリ" }),
    ...tree.map((n) =>
      el("option", { value: `${n.scope}/${n.category}`, text: `${categoryLabel(n)} (${n.count})` })
    ),
  );
  catFilter.value = current;
}

/** 絞り込みの選択を {scope, category} に戻す。 */
function categorySelection() {
  if (!catFilter.value) return { scope: null, category: null };
  const cut = catFilter.value.indexOf("/");
  return {
    scope: catFilter.value.slice(0, cut),
    category: catFilter.value.slice(cut + 1),
  };
}

/**
 * タグの選択肢。**マスターが無い**ので、使われているものを数えてもらう。
 *
 * 1 つだけ選ぶ形にしてある。複数選べるようにすると「AND か OR か」を決めることに
 * なり、カテゴリの絞り込みとも見た目が揃わない。必要になってから広げる。
 */
async function loadTags() {
  let tags = [];
  try {
    tags = await api("/api/tags");
  } catch {
    tags = [];
  }
  const current = tagFilter.value;
  tagFilter.replaceChildren(
    el("option", { value: "", text: "すべてのタグ" }),
    ...tags.map((t) => el("option", { value: t.name, text: `#${t.name} (${t.count})` })),
  );
  // 選んでいたタグが最後の 1 語から外れると選択肢ごと消える。その場合は「すべて」に戻る
  tagFilter.value = current;
  tagFilter.disabled = !tags.length;
}

// ------------------------------------------------------------ カテゴリ管理

/** ローカルは 📁 を付ける。付けないと、どちらの辞書を触るのか分からない。 */
function categoryLabel(node) {
  return node.scope === "local" ? `📁 ${node.category}` : node.category;
}

/** そのスコープの中での位置。並べ替えは辞書を跨がない。 */
function siblings(scope) {
  return tree.filter((n) => n.scope === scope);
}

function categoryRow(node) {
  const nameNode = el("div", { class: "cat-row-name", text: categoryLabel(node) });
  const meta = el("div", { class: "cat-row-meta", text:
    `${node.count} 語` +
    (node.subcategories.filter((s) => s.name).length
      ? ` · ${node.subcategories.filter((s) => s.name).map((s) => s.name).join(" / ")}`
      : "") +
    (node.description ? ` · ${node.description}` : "") +
    (node.scope === "local" ? " · このフォルダの辞書" : "")
  });
  const peers = siblings(node.scope);
  const at = peers.findIndex((n) => n.category === node.category);
  const row = el("div", { class: "cat-row" }, [
    el("div", { class: "cat-row-main" }, [nameNode, meta]),
    el("button", {
      type: "button", class: "ghost", text: "↑", title: "1 つ上へ",
      "aria-label": `${node.category} を 1 つ上へ`,
      disabled: at <= 0,
      onclick: () => moveCategory(node, -1),
    }),
    el("button", {
      type: "button", class: "ghost", text: "↓", title: "1 つ下へ",
      "aria-label": `${node.category} を 1 つ下へ`,
      disabled: at < 0 || at >= peers.length - 1,
      onclick: () => moveCategory(node, 1),
    }),
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

/**
 * 1 つ上 / 下へ動かす。**送るのはそのスコープの全体の並び**（差分ではない）。
 *
 * 「これを 1 つ上へ」を送る形にすると、続けて押したときに後の書き込みが前の
 * ものを消す（関係の書き込みを 1 本ずつ PUT しない、と同じ理由）。
 */
async function moveCategory(node, delta) {
  const names = siblings(node.scope).map((n) => n.category);
  const at = names.indexOf(node.category);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= names.length) return;
  names.splice(to, 0, ...names.splice(at, 1));
  try {
    await api("/api/category-order", { method: "PUT", body: { names, scope: node.scope } });
    await refreshAll();
  } catch (err) {
    alert(`並べ替えできません: ${err.message}`);
  }
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
        // マスターは辞書ごとにあるので、どちらでも同じものを送る。
        // **サブカテゴリを送らないと空リストで上書きされる**（省略と「全部消す」
        // を区別するため、サーバ側の既定は null）
        const body = {
          name: next,
          subcategories: node.subcategories.map((s) => s.name).filter(Boolean),
        };
        await api(
          `/api/categories/${encodeURIComponent(node.category)}?scope=${node.scope}`,
          { method: "PUT", body }
        );
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
  await Promise.all([loadCategories(), loadTags()]);
  await reload();
  paintCategoryManager();
  paintEntryCount(document.getElementById("count"));
}

async function removeCategory(node) {
  const where = node.scope === "local" ? "このフォルダの辞書の" : "";
  if (!confirm(`${where}カテゴリ「${node.category}」を削除します。よろしいですか？`)) return;
  try {
    // **スコープを必ず渡す。** 渡さないと、同名のグローバルのカテゴリが消える
    await api(
      `/api/categories/${encodeURIComponent(node.category)}?scope=${node.scope}`,
      { method: "DELETE" }
    );
    await refreshAll();
  } catch (err) {
    alert(`削除できません: ${err.message}`);
  }
}

async function addCategory() {
  const input = catDialog.querySelector("[data-ref=catNew]");
  const status = catDialog.querySelector("[data-ref=catStatus]");
  const scopeSel = catDialog.querySelector("[data-ref=catScope]");
  const name = input.value.trim();
  if (!name) {
    input.focus();
    return;
  }
  // **スコープを必ず渡す。** 渡さないと、フォルダのカテゴリのつもりが
  // 全体のマスターに残る（削除と同じ形の事故）
  const scope = scopeSel.hidden ? "global" : scopeSel.value;
  setStatus(status, "登録中", "busy");
  try {
    await api(`/api/categories?scope=${scope}`, { method: "POST", body: { name } });
    input.value = "";
    setStatus(status, `「${name}」を登録しました`);
    await refreshAll();
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

/**
 * フォルダの辞書が使えるときだけ、作る先を選ばせる。
 *
 * 使えないときに選択肢を出すと、選んでからサーバに断られる。
 */
async function paintScopeChoice() {
  const scopeSel = catDialog.querySelector("[data-ref=catScope]");
  let available = false;
  try {
    available = Boolean((await api("/api/health")).local_available);
  } catch {
    available = false;
  }
  scopeSel.hidden = !available;
  if (!available) scopeSel.value = "global";
}

// ------------------------------------------------------------------- 起動

/**
 * 辞書一覧を ``host`` に描く。ページとして開いたときも、ビューアに重ねる
 * ときも、ここを通る。
 */
export async function mount(container, { search = "" } = {}) {
  host = container;
  host.innerHTML = TEMPLATE;
  list = $("list");
  qInput = $("q");
  catFilter = $("catFilter");
  tagFilter = $("tagFilter");
  catDialog = $("catDialog");
  tree = [];

  // 一覧の要約に出てきた知らない語も、その場で選んで登録できるようにする
  // （ビューア・用語ページと同じ口。ここだけ「新規登録」ボタンからしか入れなかった）
  installSelectionAdd({
    root: list,
    source: () => "辞書一覧",
    onSaved: async () => {
      invalidatePopupCache();
      await refreshAll();
    },
  });

  // **listener は最初の await より前に付ける**（その間の操作を落とさない）
  qInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(reload, 180);
  });
  catFilter.addEventListener("change", reload);
  tagFilter.addEventListener("change", reload);
  $("add").addEventListener("click", onAdd);
  $("manageCats").addEventListener("click", onManageCats);
  catDialog.querySelector("[data-ref=catAdd]").addEventListener("click", addCategory);
  catDialog.querySelector("[data-ref=catNew]").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addCategory();
    }
  });
  catDialog.querySelector("[data-ref=catClose]").addEventListener("click", () => catDialog.close());

  const initial = new URLSearchParams(search);
  qInput.value = initial.get("q") || "";
  paintEntryCount(document.getElementById("count"));

  ready = Promise.all([loadCategories(), loadTags()]).then(() => {
    // `?category=` はスコープを持たないことがある（用語ページからのリンクなど）。
    // `?scope=` があればそれで、無ければ最初に見つかったものに合わせる
    const cat = initial.get("category");
    if (cat) {
      const wanted = initial.get("scope");
      const hit = tree.find((n) => n.category === cat && (!wanted || n.scope === wanted));
      if (hit) catFilter.value = `${hit.scope}/${hit.category}`;
    }
    const tag = initial.get("tag");
    if (tag) tagFilter.value = tag;
    return reload();
  });
  await ready;
}

async function onAdd() {
  const saved = await openEntryEditor({});
  if (saved) await refreshAll();
}

async function onManageCats() {
  // 開いた時点で持っているものを描き、**ダイアログを開いてから** await する
  // （開く前に待つと、その間のクリックが黙って無視される）
  paintCategoryManager();
  catDialog.showModal();
  paintScopeChoice();
  // **読み込みが終わる前に開かれることがある。** `tree` を描くだけで描き直さない
  // 作りだと、その場合「カテゴリがまだありません」のまま固まる（読み込みが
  // 遅いときだけ起きるので気付きにくい）
  await ready;
  paintCategoryManager();
}
