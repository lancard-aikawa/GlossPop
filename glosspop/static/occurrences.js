// 巻末索引。**語 → 本文のどこに何回出てくるか**を、辞書の側から全部並べる。
//
// 用語ページの「この語が出てくる文書」は 1 語ずつで、**辞書を通して見る道が
// 無かった**。ここでいちばん見たいのは逆側 —— **登録したのに本文に 1 度も
// 出てこない語**で、それは「登録したのにリンクにならない」の事後版にあたる
// （登録前は `filter_candidates()` が防いでいる）。
//
// **索引は持たない。** 開くたびに本文を読み直すので、外のエディタで書き換えた
// ぶんも次に開けば追いつく（横断検索と同じ判断。→ CLAUDE.md）。そのぶん
// **打ち切りは必ず出す** —— 「1 度も出てこない」と「読んでいないだけ」が
// 混ざると、この索引を見る意味そのものが無くなる。
import { api, el, paintEntryCount, setStatus } from "./base.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない）。
//: id ではなく `data-ref` を使うのは、ビューアに重ねたときに同じ id が
//: 2 つある文書にならないようにするため（点検と同じ作法）
const TEMPLATE = `
<div class="entry-head">
  <h1>索引</h1>
  <p class="summary">
    開いているフォルダの本文を読んで、登録した語が<strong>どこに何回出てくるか</strong>を
    並べます。当たり方は本文の自動リンクと同じです。
  </p>
</div>
<div class="toolbar">
  <label class="check" data-ref="unseenBox">
    <input type="checkbox" data-ref="unseen">
    <span>出てこない語だけ</span>
  </label>
  <select data-ref="order" class="auto-width" aria-label="並べ替え">
    <option value="count">出現の多い順</option>
    <option value="reading">読み順</option>
    <option value="category">カテゴリ順</option>
  </select>
  <input type="search" data-ref="q" placeholder="語を絞る" aria-label="語を絞る">
  <button type="button" data-ref="reload">読み直す</button>
  <span class="spacer"></span>
  <span class="status" data-ref="status"></span>
</div>
<p class="notice" data-ref="notes" hidden></p>
<div data-ref="list">
  <p class="empty">本文を読んでいます…</p>
</div>
`;

let refs = {};
let countNode;
//: 直前に読んだ索引。**並べ替えと絞り込みでサーバへ行き直さない**
//: （本文を読み直す口なので、押すたびに走らせない）
let last = null;

/** 1 語ぶんの行。**出てくる文書はその場から開ける**（ビューアで初出へ飛ぶ）。 */
function termRow(item) {
  const bits = [
    el("a", { class: "chip", href: item.url, text: item.term, title: item.path_label }),
  ];
  if (item.reading) bits.push(el("span", { class: "hint", text: item.reading }));
  bits.push(el("span", {
    class: item.total ? "rel-rank" : "rel-reveal",
    text: item.total ? `${item.total} 回` : "出てこない",
    title: item.total ? "" : "本文に 1 度も出てきません（別名を足すか、表記を見直してください）",
  }));
  for (const file of item.files) {
    // **押したらビューアで開いて初出へ飛ぶ**（用語ページの「初出へ」と同じ形）
    const query = new URLSearchParams({ open: file.path, term: item.term });
    bits.push(el("a", {
      class: "chip",
      href: `/?${query}`,
      text: `${file.name}${file.first ? ` ${file.first}` : ""}（${file.count}）`,
      title: file.path,
    }));
  }
  // **並べきれなかったぶんは数で出す**（黙って落とさない）
  if (item.more_files) {
    bits.push(el("span", { class: "hint", text: `ほか ${item.more_files} 文書` }));
  }
  return el("li", { class: "rel-row" }, bits);
}

/** 見出し（カテゴリ順のときだけ束ねる）。ほかは 1 つの並びで出す。 */
function groupsOf(items, order) {
  if (order !== "category") return [["", items]];
  const out = new Map();
  for (const item of items) {
    // **鍵は `<scope><>カテゴリ`**（名前だけで束ねると 2 つの辞書が混ざる）
    const key = `${item.scope}<>${item.category}`;
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(item);
  }
  return [...out].map(([key, list]) => {
    const [scope, category] = key.split("<>");
    return [scope === "local" ? `📁 ${category}` : category, list];
  });
}

function sortItems(items, order) {
  const by = {
    // サーバが返す既定の並び（多い順）はそのまま使う
    count: null,
    reading: (a, b) => (a.reading || a.term).localeCompare(b.reading || b.term, "ja"),
    category: (a, b) =>
      a.category.localeCompare(b.category, "ja")
      || (a.reading || a.term).localeCompare(b.reading || b.term, "ja"),
  }[order];
  return by ? [...items].sort(by) : items;
}

function render() {
  if (!last) return;
  const needle = refs.q.value.trim().toLowerCase();
  const items = last.terms.filter((item) => {
    if (refs.unseen.checked && item.total) return false;
    if (!needle) return true;
    return `${item.term} ${item.reading} ${item.category}`.toLowerCase().includes(needle);
  });
  if (!items.length) {
    refs.list.replaceChildren(el("p", { class: "empty", text: "該当する語がありません。" }));
    return;
  }
  const order = refs.order.value;
  refs.list.replaceChildren(...groupsOf(sortItems(items, order), order).map(([head, list]) =>
    el("section", { class: "entry-section" }, [
      head ? el("h2", {}, [el("span", { text: head }),
        el("span", { class: "issue-badge warn", text: String(list.length) })]) : null,
      el("ul", { class: "rel-list" }, list.map(termRow)),
    ])));
}

/**
 * 読んだ量と打ち切りを出す。**黙って切らない。**
 *
 * 「出てこない語」は打ち切っているときは当てにならない —— そこを書かないと、
 * 読んでいないだけの語を「本文に無い」と読ませることになる。
 */
function paintNotes() {
  const lines = [];
  if (!last.files_scanned) {
    lines.push(
      "読める文書がありません（フォルダを開いていないか、中身が空です）。"
      + "ビューアの 📁 フォルダを選ぶ… で開いてください。"
    );
  }
  if (last.files_truncated) {
    // **ここは画面にそのまま出る文。`**` で囲まないこと**（記号がそのまま読まれる）
    lines.push(
      `文書が多いので ${last.files_scanned} 件で打ち切りました。`
      + "「出てこない語」はこの状態では当てになりません"
      + "（読んでいない文書にあるかもしれません）。"
    );
  }
  if (last.skipped.length) {
    lines.push(`読めなかった文書が ${last.skipped.length} 件あります（${last.skipped[0].path} など）。`);
  }
  refs.notes.hidden = !lines.length;
  refs.notes.textContent = lines.join(" / ");
}

async function refresh() {
  refs.reload.disabled = true;
  setStatus(refs.status, "本文を読んでいます", "busy");
  try {
    last = await api("/api/occurrences");
    paintNotes();
    render();
    setStatus(
      refs.status,
      `${last.checked} 語 / ${last.files_scanned} 文書 —— `
      + (last.unseen ? `${last.unseen} 語は出てきません` : "すべて本文に出てきます")
    );
  } catch (err) {
    setStatus(refs.status, err.message, "error");
    refs.list.replaceChildren(el("p", { class: "status error", text: err.message }));
  }
  refs.reload.disabled = false;
}

/**
 * 索引を ``host`` に描く。`/occurrences` を直接開いたときも、ビューアの上に
 * 重ねるときも、ここを通る（点検と同じ）。
 */
export async function mount(host) {
  host.innerHTML = TEMPLATE;
  refs = {};
  for (const node of host.querySelectorAll("[data-ref]")) {
    refs[node.dataset.ref] = node;
  }
  countNode = document.getElementById("count");   // topbar は覆いの外
  // **listener は最初の await より前に付ける**（読み込み中の操作を捨てない）
  refs.reload.addEventListener("click", refresh);
  refs.order.addEventListener("change", render);
  refs.unseen.addEventListener("change", render);
  refs.q.addEventListener("input", render);
  paintEntryCount(countNode);
  await refresh();
}
