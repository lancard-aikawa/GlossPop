// 表示中の文書から用語候補を抽出し、選んだ語をまとめて下書き → 保存する。
//
// 抽出は 1 回の AI 呼び出しで済むが、下書きは 1 語あたり数十秒かかる。
// そのため「候補を選ぶ」段階を必ず挟み、下書きは選ばれた語だけを順に作る。
import { api, defaultSpoiler, el, rememberSpoiler, setStatus } from "./base.js";
import { openEntryEditor } from "./editor.js";
import { invalidatePopupCache } from "./popup.js";

let dialog = null;
let refs = {};

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
        <button type="button" data-ref="cancel">閉じる</button>
        <button type="button" class="primary" data-ref="go" hidden></button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

/** 候補 1 件ぶんの行。DOM とデータを 1 つにまとめて持ち回る。 */
function makeRow(candidate) {
  const check = el("input", { type: "checkbox", checked: true });
  const state = el("span", { class: "status" });
  // フォルダ横断では「どのファイルに何回出たか」が選ぶ材料になる
  const where = candidate.file_count
    ? `${candidate.file_count} ファイル / ${candidate.count} 回: ${candidate.files.join(", ")}`
    : "";
  const why = [candidate.why, where, candidate.context].filter(Boolean).join(" — ");
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

function selected(rows) {
  return rows.filter((r) => r.check.checked && !r.saved);
}

/**
 * 抽出ダイアログを開く。
 *
 * @param {object} o
 * @param {string} [o.text]    表示中の文書の原文 (folder=false のとき必須)
 * @param {string} [o.source]  出典 (ファイル名や URL)
 * @param {boolean} [o.folder] true なら開いているフォルダ全体から抽出する
 * @returns {Promise<number>} 保存した語数
 */
export async function openExtractDialog({ text = "", source = "", folder = false } = {}) {
  build();
  refs.title.textContent = folder ? "フォルダから用語をまとめて登録" : "用語をまとめて登録";
  // 既定は AI におまかせ (語ごとに全体 / このフォルダを選ばせる)
  refs.scope.value = "auto";
  refs.spoiler.value = await defaultSpoiler();
  refs.spoiler.onchange = () => {
    rememberSpoiler(refs.spoiler.value);
    paintGo();
  };
  refs.list.replaceChildren();
  refs.dropped.hidden = true;
  refs.go.hidden = refs.stop.hidden = refs.toggle.hidden = true;
  refs.lead.textContent = "";
  setStatus(refs.status, "Claude が候補を抽出中 (数十秒かかります)", "busy");
  dialog.showModal();

  let rows = [];
  let saved = 0;
  let aborted = false;
  let controller = null;

  const paintGo = () => {
    const n = selected(rows).length;
    const phase = rows.some((r) => r.draft) ? "保存" : "下書き";
    const noAI = refs.spoiler.value === "position";
    refs.go.hidden = !rows.length;
    refs.go.disabled = n === 0;
    refs.go.textContent =
      phase === "保存"
        ? `チェックした ${n} 語を保存`
        : noAI
          ? `選んだ ${n} 語を取り込む（AI なし）`
          : `選んだ ${n} 語の下書きを作る`;
    refs.toggle.hidden = !rows.length;
    refs.toggle.textContent = n ? "全解除" : "全選択";
  };

  const finish = () => {
    aborted = true;
    controller?.abort();
    cleanup();
    dialog.close();
  };

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
            // フォルダ横断では、その語が出てくるファイルを出典にする
            source: row.candidate.source || source,
            spoiler: refs.spoiler.value,
            file: row.candidate.first_file || "",
            scope: refs.scope.value,
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
    paintGo();
  };

  const onGo = () => (rows.some((r) => r.draft) ? runSaves() : runDrafts());

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
    refs.stop.removeEventListener("click", onStop);
    refs.cancel.removeEventListener("click", finish);
    refs.close.removeEventListener("click", finish);
    refs.form.removeEventListener("submit", onSubmit);
  }
  refs.go.addEventListener("click", onGo);
  refs.toggle.addEventListener("click", onToggle);
  refs.stop.addEventListener("click", onStop);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  refs.form.addEventListener("submit", onSubmit);

  try {
    const res = folder
      ? await api("/api/ai/extract-folder", { method: "POST", body: {} })
      : await api("/api/ai/extract", { method: "POST", body: { text, source } });
    rows = (res.candidates || []).map(makeRow);
    if (!rows.length) {
      refs.lead.textContent = "辞書に足せそうな語は見つかりませんでした。";
      setStatus(refs.status, "");
    } else {
      const scope = folder ? `${res.files_used?.length || 0} ファイルから挙げました。` : "";
      refs.lead.textContent =
        scope + "登録する語を選んでください。下書きは 1 語あたり数十秒かかります（順に作ります）。";
      refs.list.replaceChildren(...rows.map((r) => r.li));
      for (const row of rows) {
        row.check.addEventListener("change", paintGo);
        row.edit.addEventListener("click", () => onEdit(row));
      }
      setStatus(refs.status, `候補 ${rows.length} 語`);
    }
    const notes = [];
    if (res.dropped?.length) {
      notes.push("除いた語: " + res.dropped.map((d) => `${d.term}（${d.reason}）`).join("、"));
    }
    if (res.files_skipped?.length) {
      // 黙って切らない: 渡しきれなかったファイルは名前で出す
      notes.push(
        `読まなかったファイル (${res.files_skipped.length}): ` +
          res.files_skipped.slice(0, 20).join("、")
      );
    }
    if (notes.length) {
      refs.dropped.hidden = false;
      refs.dropped.textContent = notes.join(" / ");
    }
    paintGo();
  } catch (err) {
    setStatus(refs.status, err.message, "error");
  }

  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(saved), { once: true });
  });
}
