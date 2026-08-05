// 表示中の文書から用語候補を抽出し、選んだ語をまとめて下書き → 保存する。
//
// 抽出は 1 回の AI 呼び出しで済むが、下書きは 1 語あたり数十秒かかる。
// そのため「候補を選ぶ」段階を必ず挟み、下書きは選ばれた語だけを順に作る。
//
// **その前にもう 1 段ある: 何を抜き出すか (種別) を先に決める。** 種別を指定
// せずに頼むと AI は語義説明のできる語ばかり挙げ、登場人物がまるごと落ちる。
import { api, defaultSpoiler, el, rememberSpoiler, setStatus } from "./base.js";
import { openEntryEditor } from "./editor.js";
import { invalidatePopupCache } from "./popup.js";
import { openRelationsDialog } from "./relations-draft.js";

let dialog = null;
let refs = {};

const KINDS_KEY = "glosspop.extract.kinds";

/** 前回選んだ種別。無ければ null (サーバの既定に従う)。 */
function rememberedKinds() {
  try {
    const raw = localStorage.getItem(KINDS_KEY);
    const list = raw ? JSON.parse(raw) : null;
    return Array.isArray(list) && list.length ? list : null;
  } catch {
    return null;
  }
}

function rememberKinds(list) {
  try {
    localStorage.setItem(KINDS_KEY, JSON.stringify(list));
  } catch {
    /* 保存できなくてもその回の選択は効く */
  }
}

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2 data-ref="title">用語をまとめて登録</h2>
        <div class="spacer"></div>
        <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <p class="hint" data-ref="lead"></p>
        <fieldset class="kind-picker" data-ref="kindbox">
          <legend>何を抜き出すか</legend>
          <div class="kind-list" data-ref="kinds"></div>
        </fieldset>
        <ul class="cand-list" data-ref="list"></ul>
        <p class="notice" data-ref="dropped" hidden></p>
      </div>
      <footer>
        <button type="button" class="ghost" data-ref="toggle" hidden>全解除</button>
        <select data-ref="scope" class="auto-width" title="保存先" aria-label="保存先">
          <option value="auto">保存先は AI が選ぶ</option>
          <option value="global">全体の辞書へ</option>
          <option value="local">このフォルダだけ</option>
        </select>
        <select data-ref="spoiler" class="auto-width" aria-label="ネタバレ"
                title="AI にどこまで読ませるか">
          <option value="position">初出位置だけ（AI を使わない）</option>
          <option value="first">初出の場面だけで書く</option>
          <option value="full">全文から書く（ネタバレ可）</option>
        </select>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="stop" hidden>中止</button>
        <button type="button" data-ref="aliases" hidden></button>
        <button type="button" data-ref="relations" hidden>✨ 続けて関係を探す</button>
        <button type="button" data-ref="cancel">閉じる</button>
        <button type="button" class="primary" data-ref="go" hidden></button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

/** 種別のチェックボックスを描く。返すのは [{key, check}]。 */
function paintKinds(list, chosen, onChange) {
  const boxes = list.map((kind) => {
    const check = el("input", {
      type: "checkbox",
      checked: chosen.includes(kind.key),
      onchange: onChange,
    });
    refs.kinds.append(
      el("label", { class: "check kind", title: kind.hint }, [
        check,
        el("span", { class: "kind-label", text: kind.label }),
        el("span", { class: "hint", text: kind.hint }),
      ])
    );
    return { key: kind.key, check };
  });
  return boxes;
}

/** 候補 1 件ぶんの行。DOM とデータを 1 つにまとめて持ち回る。 */
function makeRow(candidate) {
  const check = el("input", { type: "checkbox", checked: true });
  const state = el("span", { class: "status" });
  const why = [candidate.why, candidate.context].filter(Boolean).join(" — ");
  const edit = el("button", { type: "button", class: "ghost", text: "編集", hidden: true });
  const li = el("li", {}, [
    el("div", { class: "check-row" }, [
      el("label", { class: "check" }, [check, el("span", { class: "term", text: candidate.term })]),
      el("span", { class: "spacer" }),
      state,
      edit,
    ]),
    el("p", { class: "hint", text: why }),
  ]);
  return { li, check, state, edit, candidate, draft: null, saved: null };
}

/**
 * 「既存の語の別の呼び方」1 件ぶんの行。
 *
 * 新しいエントリにはしない。同じ人物が呼び方ごとに別エントリへ割れると、
 * 本文のリンク先も相関図のノードも二重になる。
 */
function makeAliasRow(alias) {
  const check = el("input", { type: "checkbox", checked: true });
  const state = el("span", { class: "status" });
  const li = el("li", {}, [
    el("div", { class: "check-row" }, [
      el("label", { class: "check" }, [
        check,
        el("span", { class: "term", text: alias.term }),
        el("span", { class: "rel-arrow", text: "→" }),
        el("span", { text: `${alias.alias_of}（${alias.path_label}）` }),
      ]),
      el("span", { class: "spacer" }),
      state,
    ]),
    el("p", { class: "hint", text: [alias.why, alias.context].filter(Boolean).join(" — ") }),
  ]);
  return { li, check, state, alias, saved: false };
}

/** 種別ごとの見出しを挟んだ行の並びを返す。 */
function groupRows(rows) {
  const out = [];
  let last = null;
  for (const row of rows) {
    const label = row.candidate.kind_label || "その他";
    if (label !== last) {
      out.push(el("li", { class: "cand-group", text: label }));
      last = label;
    }
    out.push(row.li);
  }
  return out;
}

function selected(rows) {
  return rows.filter((r) => r.check.checked && !r.saved);
}

/**
 * 抽出ダイアログを開く。
 *
 * **読むのは渡された 1 文書だけ。** フォルダ全体を読む道は畳んだ ——
 * 何ファイルまとめてもサーバが AI に渡せる本文の枠は同じなので、
 * 待ち時間だけ増えて取り分が薄まる（→ docs/design-notes.md）。
 *
 * @param {object} o
 * @param {string} [o.text]    表示中の文書の原文 (必須)
 * @param {string} [o.source]  出典 (ファイル名や URL)
 * @returns {Promise<number>} 保存した語数
 */
export async function openExtractDialog({ text = "", source = "" } = {}) {
  build();
  refs.title.textContent = "用語をまとめて登録";
  // 既定は AI におまかせ (語ごとに全体 / このフォルダを選ばせる)
  refs.scope.value = "auto";
  refs.spoiler.value = await defaultSpoiler();
  refs.spoiler.onchange = () => {
    rememberSpoiler(refs.spoiler.value);
    paintGo();
  };
  refs.list.replaceChildren();
  refs.kinds.replaceChildren();
  refs.kindbox.hidden = false;
  refs.kindbox.disabled = false;
  refs.dropped.hidden = true;
  refs.go.hidden = refs.stop.hidden = refs.toggle.hidden = true;
  refs.aliases.hidden = refs.relations.hidden = true;
  refs.lead.textContent =
    "抜き出すものを先に選んでください。種別ごとに別々の枠で挙げるので、" +
    "人物と専門用語が枠を取り合いません。";
  setStatus(refs.status, "");
  dialog.showModal();

  let rows = [];
  let aliasRows = [];
  let kindBoxes = [];
  let saved = 0;
  let aborted = false;
  let busy = false;
  let controller = null;

  const pickedKinds = () => kindBoxes.filter((k) => k.check.checked).map((k) => k.key);
  const pickedAliases = () => aliasRows.filter((r) => r.check.checked && !r.saved);

  /** 別名ボタンは、足せるものが残っているときだけ出す。 */
  const paintAliases = () => {
    const n = pickedAliases().length;
    refs.aliases.hidden = !n;
    refs.aliases.textContent = `チェックした ${n} 件を別名に追加`;
  };

  const paintGo = () => {
    paintAliases();
    refs.go.hidden = false;
    // 段は 3 つ: 種別を選ぶ → 候補を選ぶ → 下書きを確認して保存する
    if (!rows.length) {
      const n = pickedKinds().length;
      refs.go.disabled = busy || n === 0;
      refs.go.textContent = n ? `${n} 種別で候補を抽出する` : "抜き出すものを選んでください";
      refs.toggle.hidden = true;
      return;
    }
    const n = selected(rows).length;
    const phase = rows.some((r) => r.draft) ? "保存" : "下書き";
    const noAI = refs.spoiler.value === "position";
    refs.go.disabled = busy || n === 0;
    refs.go.textContent =
      phase === "保存"
        ? `チェックした ${n} 語を保存`
        : noAI
          ? `選んだ ${n} 語を取り込む（AI なし）`
          : `選んだ ${n} 語の下書きを作る`;
    refs.toggle.hidden = false;
    refs.toggle.textContent = n ? "全解除" : "全選択";
  };

  // 閉じる操作は「閉じる」ボタンだけでなく Esc でも起きる。中断・後始末・解決は
  // すべて close イベントの側に寄せて、どちらの経路でも同じになるようにする
  const finish = () => dialog.close();

  const onToggle = () => {
    const turnOn = selected(rows).length === 0;
    for (const r of rows) if (!r.saved) r.check.checked = turnOn;
    paintGo();
  };

  const onStop = () => {
    aborted = true;
    controller?.abort();
    setStatus(refs.status, "中止しました", "error");
  };

  /** 選んだ種別で候補を挙げさせる。AI 呼び出しは 1 回だけ。 */
  const runExtract = async () => {
    const kinds = pickedKinds();
    if (!kinds.length) return;
    rememberKinds(kinds);
    aborted = false;
    busy = true;
    refs.kindbox.disabled = true;
    refs.stop.hidden = false;
    paintGo();
    setStatus(refs.status, "AI が候補を抽出中 (数十秒かかります)", "busy");
    try {
      controller = new AbortController();
      const res = await api("/api/ai/extract", {
        method: "POST",
        signal: controller.signal,
        body: { text, source, kinds },
      });
      controller = null;
      rows = (res.candidates || []).map(makeRow);
      aliasRows = (res.aliases || []).map(makeAliasRow);
      if (!rows.length && !aliasRows.length) {
        refs.lead.textContent = "選んだ種別に当てはまる語は見つかりませんでした。";
        setStatus(refs.status, "");
      } else {
        refs.lead.textContent =
          "登録する語を選んでください。下書きは 1 語あたり数十秒かかります（順に作ります）。";
        const listed = [...groupRows(rows)];
        if (aliasRows.length) {
          // 既存の語に足すだけなので下書きは要らない。別の枠として先に見せる
          listed.unshift(
            el("li", { class: "cand-group", text: "別の呼び方（既存の語に足す）" }),
            ...aliasRows.map((r) => r.li)
          );
        }
        refs.list.replaceChildren(...listed);
        for (const row of rows) {
          row.check.addEventListener("change", paintGo);
          row.edit.addEventListener("click", () => onEdit(row));
        }
        for (const row of aliasRows) row.check.addEventListener("change", paintGo);
        setStatus(
          refs.status,
          [`候補 ${rows.length} 語`, aliasRows.length ? `別名 ${aliasRows.length} 件` : ""]
            .filter(Boolean)
            .join(" / ")
        );
      }
      paintNotes(res);
    } catch (err) {
      // 閉じられて中断したときは、消えたダイアログにエラーを書きに行かない
      if (!aborted) setStatus(refs.status, err.message, "error");
      refs.kindbox.disabled = false;   // やり直せるように戻す
    }
    busy = false;
    refs.stop.hidden = true;
    paintGo();
  };

  /** 除いた語を理由つきで出す（黙って切らない）。 */
  const paintNotes = (res) => {
    const notes = [];
    if (res.dropped?.length) {
      notes.push("除いた語: " + res.dropped.map((d) => `${d.term}（${d.reason}）`).join("、"));
    }
    refs.dropped.hidden = !notes.length;
    refs.dropped.textContent = notes.join(" / ");
  };

  /** 選ばれた語の下書きを順に作る。1 語ずつなので進捗を出し、中止も効く。 */
  const runDrafts = async () => {
    const targets = selected(rows);
    if (!targets.length) return;
    aborted = false;
    refs.go.disabled = refs.toggle.disabled = true;
    refs.stop.hidden = false;
    let done = 0;
    for (const row of targets) {
      if (aborted) break;
      setStatus(refs.status, `下書き ${done + 1}/${targets.length}: ${row.candidate.term}`, "busy");
      setStatus(row.state, "生成中", "busy");
      controller = new AbortController();
      try {
        const res = await api("/api/ai/draft", {
          method: "POST",
          body: {
            term: row.candidate.term,
            context: row.candidate.context,
            source,
            spoiler: refs.spoiler.value,
            scope: refs.scope.value,
            // 抽出時の種別。保存先 (人物ならこのフォルダの辞書) の下敷きになる
            kind: row.candidate.kind || "",
          },
          signal: controller.signal,
        });
        row.draft = res.draft;
        const label = [res.draft.category, res.draft.subcategory].filter(Boolean).join(" / ");
        const first = res.draft.first_locator
          ? `初出 ${res.draft.first_file} ${res.draft.first_locator}`
          : "";
        // どちらの辞書に入るかは語ごとに変わるので、行ごとに見せる
        const mark = res.draft.scope === "local" ? "📁 " : "";
        setStatus(row.state, mark + (label || "カテゴリ未定"));
        row.li.querySelector(".hint").textContent =
          [res.draft.summary, first].filter(Boolean).join(" — ");
        row.edit.hidden = false;
      } catch (err) {
        if (aborted) break;
        setStatus(row.state, err.message, "error");
        row.check.checked = false;
      }
      done++;
    }
    controller = null;
    refs.stop.hidden = true;
    refs.toggle.disabled = false;
    const made = rows.filter((r) => r.draft).length;
    if (made) {
      refs.lead.textContent =
        "内容を確認して、保存する語にチェックを残してください。個別に直すなら「編集」から開けます。";
    }
    setStatus(
      refs.status,
      aborted ? `中止しました（${made} 語ぶんできています）` : `${made} 語の下書きができました。確認して保存してください。`,
      aborted ? "error" : ""
    );
    paintGo();
  };

  /** 下書きができている語をまとめて保存する。 */
  const runSaves = async () => {
    const targets = selected(rows).filter((r) => r.draft);
    if (!targets.length) return;
    refs.go.disabled = refs.toggle.disabled = true;
    let done = 0;
    for (const row of targets) {
      setStatus(refs.status, `保存 ${done + 1}/${targets.length}: ${row.candidate.term}`, "busy");
      try {
        row.saved = await api("/api/entries", {
          method: "POST",
          // 「AI が選ぶ」なら下書きに入っている保存先をそのまま使う
          body: {
            ...row.draft,
            scope: refs.scope.value === "auto" ? row.draft.scope || "global" : refs.scope.value,
          },
        });
        saved++;
        setStatus(row.state, `保存しました (${row.saved.path_label})`);
        row.check.checked = false;
        row.check.disabled = true;
        row.edit.hidden = true;
      } catch (err) {
        setStatus(row.state, err.message, "error");
      }
      done++;
    }
    if (saved) invalidatePopupCache();
    refs.toggle.disabled = false;
    setStatus(refs.status, `${saved} 語を保存しました`);
    // 登録しただけでは関係が空のまま。ここで続きへ渡す
    // （相関図まで移動して押し直させると、たいてい忘れられる）
    refs.relations.hidden = saved < 2;
    paintGo();
  };

  /**
   * 「別の呼び方」を既存エントリの別名としてまとめて足す。
   *
   * まとめて送るのは関係と同じ理由。同じ人物に別名が 2 つ付くとき、1 件ずつ
   * 送るとサーバ側の読み書きが競って先に書いたぶんが消える。
   */
  const onAliases = async () => {
    const targets = pickedAliases();
    if (!targets.length) return;
    refs.aliases.disabled = true;
    setStatus(refs.status, `別名 ${targets.length} 件を追加中`, "busy");
    try {
      const res = await api("/api/aliases", {
        method: "POST",
        body: { aliases: targets.map((r) => ({ ref: r.alias.ref, alias: r.alias.term })) },
      });
      const failed = new Map(res.results.filter((x) => !x.ok).map((x) => [x.ref, x.detail]));
      for (const row of targets) {
        const bad = failed.get(row.alias.ref);
        if (bad) {
          setStatus(row.state, bad, "error");
          continue;
        }
        row.saved = true;
        row.check.checked = false;
        row.check.disabled = true;
        setStatus(row.state, "別名に追加しました");
      }
      if (res.applied) invalidatePopupCache();   // 本文のリンクが増える
      setStatus(refs.status, `別名を ${res.applied} 件追加しました`);
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.aliases.disabled = false;
    paintGo();
  };

  /** 登録した語どうしの関係を続けて探す。同じ本文をそのまま渡す。 */
  const onRelations = async () => {
    const categories = [
      ...new Set(rows.filter((r) => r.saved).map((r) => r.saved.category)),
    ];
    refs.relations.disabled = true;
    try {
      // カテゴリが 1 つに絞れるならそこだけ、混ざっていれば辞書全体で探す。
      // 本文はいま抽出に使ったものをそのまま渡す（サーバは読み直さない）
      await openRelationsDialog({
        category: categories.length === 1 ? categories[0] : "",
        text,
        source,
      });
    } finally {
      refs.relations.disabled = false;
    }
  };

  // 主ボタンは 1 つで、段によって役割が変わる (種別を選ぶ → 抽出 → 下書き → 保存)
  const onGo = () => {
    if (!rows.length) return runExtract();
    return rows.some((r) => r.draft) ? runSaves() : runDrafts();
  };

  const onEdit = async (row) => {
    const origin = row.candidate.source || source;
    const result = await openEntryEditor({
      term: row.candidate.term,
      context: row.candidate.context,
      source: origin,
      scope: refs.scope.value,
      entry: { ...row.draft, term: row.draft?.term || row.candidate.term, source: origin },
    });
    if (!result) return;
    row.saved = result;
    saved++;
    setStatus(row.state, `保存しました (${result.path_label})`);
    row.check.checked = false;
    row.check.disabled = true;
    row.edit.hidden = true;
    paintGo();
  };

  const onSubmit = (ev) => ev.preventDefault();
  function cleanup() {
    refs.go.removeEventListener("click", onGo);
    refs.toggle.removeEventListener("click", onToggle);
    refs.aliases.removeEventListener("click", onAliases);
    refs.relations.removeEventListener("click", onRelations);
    refs.stop.removeEventListener("click", onStop);
    refs.cancel.removeEventListener("click", finish);
    refs.close.removeEventListener("click", finish);
    refs.form.removeEventListener("submit", onSubmit);
  }
  refs.go.addEventListener("click", onGo);
  refs.toggle.addEventListener("click", onToggle);
  refs.aliases.addEventListener("click", onAliases);
  refs.relations.addEventListener("click", onRelations);
  refs.stop.addEventListener("click", onStop);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  refs.form.addEventListener("submit", onSubmit);

  // **promise は抽出を待つ前に用意する。** 抽出は数十秒かかり、その間に閉じられる。
  // await のあとで listener を付けると close イベントを取り逃がして永久に解決せず、
  // 呼び出し側の finally が走らないので「✨ 用語を抽出」が押せないままになる
  let closed = null;
  const done = new Promise((resolve) => {
    closed = () => {
      aborted = true;
      controller?.abort();   // 閉じたら AI 呼び出しも止める (裏で走り続けさせない)
      cleanup();
      resolve(saved);
    };
  });
  dialog.addEventListener("close", closed, { once: true });

  // 種別の一覧はサーバが持っている (ai.EXTRACT_KINDS)。抽出はここでは始めない
  // —— 何を抜き出すかを選んでもらってからでないと、人物が落ちた結果しか出ない
  try {
    const spec = await api("/api/ai/kinds");
    const chosen = rememberedKinds() || spec.default || [];
    kindBoxes = paintKinds(spec.kinds || [], chosen, paintGo);
  } catch (err) {
    if (!aborted) setStatus(refs.status, err.message, "error");
  }
  paintGo();

  return done;
}
