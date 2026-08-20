// 割れてしまった同じものを 1 つにまとめるダイアログ。辞書ページから開く。
//
// **2 段構え。** 相手を選ぶ → 何がどうなるかを見てから実行する。これは
// データを消す操作なので、押した瞬間に畳むのではなく、畳めない項目
// (本文・要約・同じ相手への関係) を人に決めさせる。
import { api, el, esc, setStatus } from "./base.js";

//: 衝突したときに選ばせる項目。**サーバ (`merge.CONFLICT_FIELDS`) と同じ並び**
const FIELD_LABELS = {
  reading: "読み",
  summary: "要約",
  definition: "本文",
  source: "出典",
  first_file: "初出のファイル",
  first_locator: "初出の位置",
  when: "作中の時刻",
};

let dialog = null;
let refs = {};

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <header>
      <h2>まとめる</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
    </header>
    <div class="body" data-ref="body"></div>
    <footer>
      <span class="status" data-ref="status"></span>
      <span class="spacer"></span>
      <button type="button" data-ref="cancel">やめる</button>
      <button type="button" class="primary" data-ref="ok" hidden>まとめる</button>
    </footer>`;
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  // **listener は最初の await より前に付ける。** ダイアログは開いた瞬間から
  // 操作できるので、読み込みを待ってから付けるとその間の操作が黙って消える
  refs.close.addEventListener("click", () => dialog.close());
  refs.cancel.addEventListener("click", () => dialog.close());
  document.body.append(dialog);
  return dialog;
}

/**
 * まとめる相手を選ばせ、下見を見せて実行する。
 *
 * まとめた結果の ref を返す (やめたときは null)。**検知はしない** ——
 * 「同じ人物かもしれない」を機械で挙げるとカテゴリ違いの同名を大量に拾い、
 * 警告が読まれなくなる。候補として出すのは同じ表記のものだけで、
 * あとは自分で探してもらう。
 */
export async function openMerge(entry, candidates) {
  build();
  refs.ok.hidden = true;
  refs.ok.disabled = false;      // 前回失敗したまま開き直しても押せること
  setStatus(refs.status, "");
  paintPicker(entry, candidates);
  dialog.returnValue = "";
  dialog.showModal();

  await new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
  return dialog.returnValue || null;
}

// ------------------------------------------------------------ 相手を選ぶ

function paintPicker(entry, candidates) {
  const box = el("div", { class: "merge-pick" });
  box.append(el("p", { class: "hint", text:
    `「${entry.term}」に、別のエントリをまとめます。` +
    "まとめる側は消え、その用語名は「" + entry.term + "」の別名になります。" }));

  const search = el("input", {
    type: "search", placeholder: "用語名で探す", "aria-label": "まとめる相手を探す",
    autocomplete: "off",
  });
  const results = el("div", { class: "merge-results" });
  box.append(search, results);

  const show = (list) => {
    if (!list.length) {
      results.replaceChildren(el("p", { class: "empty", text: "候補がありません" }));
      return;
    }
    results.replaceChildren(...list.map((e) =>
      el("button", { type: "button", class: "merge-cand", onclick: () => showPlan(entry, e) }, [
        el("span", { class: "t", text: e.term }),
        el("span", { class: "s", text: `${e.path_label}${e.summary ? ` · ${e.summary}` : ""}` }),
      ])
    ));
  };

  // 同じ表記のものを最初から出す。いちばんよくある「割れ方」がこれ
  show(candidates);
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) return show(candidates);
      try {
        const hits = await api(`/api/entries?q=${encodeURIComponent(q)}`);
        show(hits.filter((e) => e.ref !== entry.ref));
      } catch (err) {
        results.replaceChildren(el("p", { class: "status error", text: err.message }));
      }
    }, 180);
  });

  refs.body.replaceChildren(box);
  refs.ok.hidden = true;
  search.focus();
}

// ------------------------------------------------------------ 下見と実行

async function showPlan(keep, dropCard) {
  setStatus(refs.status, "下見を作っています", "busy");
  let plan;
  try {
    plan = await api(
      `/api/merge?keep=${encodeURIComponent(keep.ref)}&drop=${encodeURIComponent(dropCard.ref)}`
    );
  } catch (err) {
    setStatus(refs.status, err.message, "error");
    return;
  }
  setStatus(refs.status, "");

  // 選ばれた値をここに集める。**既定は残す側**（サーバ側の既定と揃える）
  const chosenFields = {};
  const chosenRelations = new Map();

  const parts = [
    // `.rel-sub` は使わない —— `text-transform: uppercase` が付いているので、
    // カテゴリ名に英字が入ると `api` が `API` になる（日本語専用の小見出し）
    el("p", { class: "hint", html:
      `<strong>${esc(plan.keep.term)}</strong> <span class="count">${esc(plan.keep.path_label)}</span>` +
      ` に <strong>${esc(plan.drop.term)}</strong> <span class="count">${esc(plan.drop.path_label)}</span>` +
      ` をまとめます。` }),
  ];
  for (const text of plan.warnings) {
    parts.push(el("p", { class: "notice", text }));
  }

  parts.push(section("引き継ぐもの", el("ul", { class: "merge-union" }, [
    li("別名", plan.union.aliases),
    li("当てない表記", plan.union.excludes),
    li("タグ", plan.union.tags),
    li("使用例", plan.union.examples.map((s) => s.slice(0, 40))),
  ].filter(Boolean))));

  if (plan.conflicts.length) {
    parts.push(section("どちらを残すか", el("div", { class: "merge-conflicts" },
      plan.conflicts.map((c) => conflictRow(c, chosenFields)))));
  }

  const relRows = plan.relations.filter((r) => !r.self_reference);
  if (relRows.length) {
    parts.push(section("関係", el("div", { class: "merge-relations" },
      relRows.map((r) => relationRow(r, chosenRelations)))));
  }

  if (plan.backlinks.length) {
    // **参照側は書き換えない。** 転送で解決し続けることを言っておかないと、
    // 「消したら他の関係が切れるのでは」と不安になる
    parts.push(section("このエントリを指している側", el("p", { class: "hint", text:
      plan.backlinks.map((b) => b.term).join("、") +
      " の関係は書き換えません（転送されるのでそのまま解決します）" })));
  }

  refs.body.replaceChildren(...parts);
  refs.body.scrollTop = 0;
  refs.ok.hidden = false;
  refs.ok.onclick = () => apply(plan, chosenFields, chosenRelations);
}

function section(title, body) {
  return el("section", { class: "merge-section" }, [el("h3", { text: title }), body]);
}

function li(label, values) {
  if (!values.length) return null;
  return el("li", { text: `${label}: ${values.join(" / ")}` });
}

/** 1 項目ぶんのラジオ。**既定は残す側**（何も触らなければ残す側が採られる）。 */
function conflictRow(conflict, chosen) {
  const name = `merge-${conflict.field}`;
  const pick = (value) => { chosen[conflict.field] = value; };
  return el("div", { class: "merge-conflict" }, [
    el("h4", { text: FIELD_LABELS[conflict.field] || conflict.field }),
    choice(name, conflict.keep, true, () => pick(conflict.keep)),
    choice(name, conflict.drop, false, () => pick(conflict.drop)),
  ]);
}

function choice(name, value, checked, onpick) {
  const input = el("input", { type: "radio", name, checked });
  input.addEventListener("change", onpick);
  return el("label", { class: "merge-choice" }, [input, el("span", { text: value })]);
}

/**
 * 同じ相手への関係が両側にあるとき、どちらの一言を採るか。
 *
 * 片側にしか無いものは選ぶまでもないので、そのまま出すだけ（送らなければ
 * サーバの既定で拾われるが、**明示的に送る**ほうが画面と結果が一致する）。
 */
function relationRow(row, chosen) {
  const key = row.key;
  chosen.set(key, row.keep || row.drop);
  const target = (row.keep || row.drop).term;
  if (!row.conflict) {
    const only = row.keep || row.drop;
    return el("div", { class: "merge-relation" }, [
      el("span", { class: "t", text: target }),
      el("span", { class: "s", text: describe(only) }),
    ]);
  }
  const name = `merge-rel-${key}`;
  return el("div", { class: "merge-relation conflict" }, [
    el("span", { class: "t", text: target }),
    choice(name, describe(row.keep), true, () => chosen.set(key, row.keep)),
    choice(name, describe(row.drop), false, () => chosen.set(key, row.drop)),
  ]);
}

function describe(rel) {
  const bits = [rel.label || "（一言なし）"];
  if (rel.back) bits.push(`↔ ${rel.back}`);
  if (rel.rank) bits.push(rel.rank);
  if (rel.reveal) bits.push(`判明: ${rel.reveal}`);
  if (rel.when) bits.push(`作中: ${rel.when}`);
  return bits.join(" · ");
}

async function apply(plan, fields, relationChoices) {
  refs.ok.disabled = true;
  setStatus(refs.status, "まとめています", "busy");
  try {
    const merged = await api("/api/merge", {
      method: "POST",
      body: {
        keep: plan.keep.ref,
        drop: plan.drop.ref,
        fields,
        // **関係の項目を足したらここも足す。** 並べ書きなので、忘れると
        // まとめた瞬間だけその項目が静かに消える（→ CLAUDE.md の一緒に直す表）
        relations: [...relationChoices.values()].map((r) => ({
          to: r.to, label: r.label, back: r.back, rank: r.rank,
          reveal: r.reveal, when: r.when,
        })),
      },
    });
    dialog.close(merged.ref);
  } catch (err) {
    setStatus(refs.status, err.message, "error");
    refs.ok.disabled = false;
  }
}
