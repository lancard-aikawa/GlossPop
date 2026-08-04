// 辞書の点検。壊れているものを種類ごとにまとめて出す。
//
// 参照を名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに切れる。
// 相関図はカテゴリ単位でしか壊れを見せないので、横断して集めるのがこのページ。
// 壊れた参照は「次に書くべきエントリ」でもあるので、そこから登録に入れる。
import { api, el, paintEntryCount, setStatus } from "./base.js";
import { encodePath, openEntryEditor } from "./editor.js";
import { invalidatePopupCache } from "./popup.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない）。
//: id を使わず `data-ref` にしてあるのは、ビューアに重ねたときに**同じ id が
//: 2 つある文書**にならないようにするため（ビューアにも `#root` がある）
const TEMPLATE = `
<div class="entry-head">
  <h1>辞書の点検</h1>
  <p class="summary">
    壊れているものだけを挙げます。カテゴリ違いの同名や、関係が書かれていない語は
    正常なので出しません。
  </p>
</div>
<div class="toolbar">
  <button type="button" data-ref="reload">もう一度点検する</button>
  <span class="spacer"></span>
  <span class="status" data-ref="status"></span>
</div>
<div data-ref="report">
  <p class="empty">点検中…</p>
</div>
`;

let root, statusNode, countNode, reloadButton;

/** その場で編集ダイアログを開く。ページを渡り歩かせない。 */
async function fix(ref) {
  let entry;
  try {
    entry = await api(`/api/entries/${encodePath(ref)}`);
  } catch (err) {
    setStatus(statusNode, err.message, "error");
    return;
  }
  if (!(await openEntryEditor({ ref, entry }))) return;
  invalidatePopupCache();
  await refresh();       // 直したぶんが消えるところまで見せる
}

/** 問題 1 件ぶんの行。直しに行ける導線を必ず付ける。 */
function issueRow(issue) {
  const bits = [
    el("a", { class: "chip", href: issue.url, text: issue.term, title: issue.path_label }),
    el("span", { class: "issue-detail", text: issue.detail }),
  ];
  // 未登録の相手は wiki の赤リンク。ここから登録に入れる
  if (issue.create_url) {
    bits.push(el("a", { class: "chip missing", href: issue.create_url, text: `${issue.target} を登録` }));
  }
  for (const c of issue.candidates || []) {
    bits.push(el("a", { class: "chip", href: c.url, text: c.path_label }));
  }
  bits.push(el("span", { class: "spacer" }));
  bits.push(el("button", {
    type: "button",
    class: "ghost",
    text: "直す",
    onclick: () => fix(issue.ref),
  }));
  return el("li", { class: "rel-row" }, bits);
}

function issueGroup(kind, issues) {
  const head = issues[0];
  return el("section", { class: "entry-section" }, [
    el("h2", {}, [
      el("span", { text: head.label }),
      el("span", { class: `issue-badge ${head.severity}`, text: String(issues.length) }),
    ]),
    el("p", { class: "hint", text: head.hint }),
    el("ul", { class: "rel-list" }, issues.map(issueRow)),
  ]);
}

function render(report) {
  if (!report.issues.length) {
    root.replaceChildren(
      el("p", { class: "empty", text: `${report.checked} 語を見ました。直すところはありません。` })
    );
    return;
  }
  // 種類ごとにまとめる。順序はサーバが付けた重大度の順を保つ
  const groups = new Map();
  for (const issue of report.issues) {
    if (!groups.has(issue.kind)) groups.set(issue.kind, []);
    groups.get(issue.kind).push(issue);
  }
  root.replaceChildren(...[...groups].map(([kind, issues]) => issueGroup(kind, issues)));
}

async function refresh() {
  reloadButton.disabled = true;
  setStatus(statusNode, "点検中", "busy");
  try {
    const report = await api("/api/doctor");
    render(report);
    setStatus(
      statusNode,
      report.issues.length
        ? `${report.checked} 語中 ${report.errors} 件の壊れ / ${report.warnings} 件の注意`
        : `${report.checked} 語 — 問題なし`
    );
  } catch (err) {
    setStatus(statusNode, err.message, "error");
    root.replaceChildren(el("p", { class: "status error", text: err.message }));
  }
  reloadButton.disabled = false;
}

/**
 * 点検を ``host`` に描く。`/doctor` を直接開いたときも、ビューアの上に
 * 重ねるときも、ここを通る。
 */
export async function mount(host) {
  host.innerHTML = TEMPLATE;
  root = host.querySelector("[data-ref=report]");
  statusNode = host.querySelector("[data-ref=status]");
  reloadButton = host.querySelector("[data-ref=reload]");
  countNode = document.getElementById("count");   // topbar は覆いの外
  reloadButton.addEventListener("click", refresh);
  paintEntryCount(countNode);
  await refresh();
}
