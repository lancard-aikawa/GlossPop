// 辞書の点検。壊れているものを種類ごとにまとめて出す。
//
// 参照を名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに切れる。
// 相関図はカテゴリ単位でしか壊れを見せないので、横断して集めるのがこのページ。
// 壊れた参照は「次に書くべきエントリ」でもあるので、そこから登録に入れる。
import { api, el, paintEntryCount, setStatus } from "./base.js";

const root = document.getElementById("root");
const statusNode = document.getElementById("status");
const countNode = document.getElementById("count");
const reloadButton = document.getElementById("reload");

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

paintEntryCount(countNode);
reloadButton.addEventListener("click", refresh);
refresh();
