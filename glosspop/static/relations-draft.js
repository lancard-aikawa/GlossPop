// 登録済みの用語どうしの関係を AI に下書きさせ、選んだものをまとめて書き込む。
//
// 関係のデータ構造だけあっても 1 本ずつ手で書くことになり、図が空のまま終わる。
// ここがそれを埋める側。**用語は作らない**（それは extract.js の仕事）ので、
// 下書きは 1 回の呼び出しで済む。まとめて登録のような語数ぶんのループは無い。
import { api, defaultSpoiler, el, rememberSpoiler, setStatus } from "./base.js";

let dialog = null;
let refs = {};

const RANK_MARK = { 上: "▲ 相手が上", 下: "▼ 相手が下", 対等: "＝ 対等" };

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2>関係を下書きする</h2>
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
        <select data-ref="spoiler" class="auto-width" aria-label="ネタバレ"
                title="AI にどこまで読ませるか">
          <option value="first">各用語の初出の場面だけを読む</option>
          <option value="full">全文を読む（ネタバレ可）</option>
        </select>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="stop" hidden>中止</button>
        <button type="button" data-ref="cancel">閉じる</button>
        <button type="button" class="primary" data-ref="go"></button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

/** 関係 1 本ぶんの行。向きと上下が一目で分かる形にする。 */
function makeRow(rel) {
  const check = el("input", { type: "checkbox", checked: true });
  const state = el("span", { class: "status" });
  const words = rel.mutual && rel.back !== rel.label
    ? `${rel.label} ⇄ ${rel.back}`
    : rel.label;
  const head = el("span", { class: "term" }, [
    el("span", { text: rel.from_term }),
    el("span", { class: "rel-arrow", text: rel.mutual ? "⇄" : "→" }),
    el("span", { text: rel.to_term }),
  ]);
  const marks = [
    el("span", { class: "rel-label", text: words }),
    rel.rank ? el("span", { class: "rel-rank", text: RANK_MARK[rel.rank] }) : null,
    rel.reveal ? el("span", { class: "rel-reveal", text: `判明: ${rel.reveal}` }) : null,
  ].filter(Boolean);

  const li = el("li", {}, [
    el("div", { class: "check-row" }, [
      el("label", { class: "check" }, [check, head]),
      ...marks,
      el("span", { class: "spacer" }),
      state,
    ]),
    el("p", { class: "hint", text: rel.why || "" }),
  ]);
  return { li, check, state, rel, saved: false };
}

/**
 * 関係の下書きダイアログを開く。
 *
 * @param {object} o
 * @param {string} [o.category] 対象カテゴリ（空なら辞書全体）
 * @param {string} [o.scope]    global | local（空なら両方）
 * @param {string} [o.text]     読ませる本文。渡すとフォルダではなくこれを読む
 *   （URL を読んでいるときはフォルダに本文が無いので、これが唯一の経路）
 * @param {string} [o.source]   本文の出典（ファイル名や URL）
 * @returns {Promise<number>} 書き込んだ関係の本数
 */
export async function openRelationsDialog({
  category = "", scope = "", text = "", source = "",
} = {}) {
  build();
  refs.list.replaceChildren();
  refs.dropped.hidden = true;
  refs.stop.hidden = refs.toggle.hidden = true;
  const where = category ? `「${category}」` : "辞書全体";
  const from = text ? (source ? `「${source}」` : "表示中の文書") : "開いているフォルダの本文";
  refs.lead.textContent =
    `${where}に登録済みの用語どうしの関係を、${from}から探します。` +
    "用語は作りません（すでに登録されているものだけを結びます）。";
  // 既定は初出の場面だけ。相関図は本文より先を一望させるので、伏せる側に倒す
  const remembered = await defaultSpoiler();
  refs.spoiler.value = remembered === "full" ? "full" : "first";
  refs.spoiler.onchange = () => rememberSpoiler(refs.spoiler.value);
  setStatus(refs.status, "");
  dialog.showModal();

  let rows = [];
  let applied = 0;
  let aborted = false;
  let busy = false;
  let controller = null;

  const selected = () => rows.filter((r) => r.check.checked && !r.saved);

  const paintGo = () => {
    if (!rows.length) {
      refs.go.disabled = busy;
      refs.go.textContent = "関係を探す";
      refs.toggle.hidden = true;
      return;
    }
    const n = selected().length;
    refs.go.disabled = busy || n === 0;
    refs.go.textContent = `チェックした ${n} 本を書き込む`;
    refs.toggle.hidden = false;
    refs.toggle.textContent = n ? "全解除" : "全選択";
  };

  const runDraft = async () => {
    busy = true;
    aborted = false;
    refs.stop.hidden = false;
    refs.spoiler.disabled = true;
    paintGo();
    setStatus(refs.status, "Claude が関係を探しています (数十秒かかります)", "busy");
    try {
      controller = new AbortController();
      const res = await api("/api/ai/relations", {
        method: "POST",
        body: { category, scope, text, source, spoiler: refs.spoiler.value },
        signal: controller.signal,
      });
      controller = null;
      rows = (res.relations || []).map(makeRow);
      if (!rows.length) {
        refs.lead.textContent = "本文から読み取れる新しい関係は見つかりませんでした。";
      } else {
        refs.lead.textContent =
          "書き込む関係にチェックを残してください。関係は片側にだけ書かれ、" +
          "相手のページには「この語を指している側」として出ます。";
        refs.list.replaceChildren(...rows.map((r) => r.li));
        for (const row of rows) row.check.addEventListener("change", paintGo);
      }
      setStatus(refs.status, `候補 ${rows.length} 本`);
      paintNotes(res);
    } catch (err) {
      if (!aborted) setStatus(refs.status, err.message, "error");
      refs.spoiler.disabled = false;    // やり直せるように戻す
    }
    busy = false;
    refs.stop.hidden = true;
    paintGo();
  };

  /** 除いた関係と読まなかったファイルを出す（黙って切らない）。 */
  const paintNotes = (res) => {
    const notes = [];
    if (res.dropped?.length) {
      notes.push(
        "除いた関係: " +
          res.dropped.map((d) => `${d.from}→${d.to}（${d.reason}）`).join("、")
      );
    }
    if (res.files_skipped?.length) {
      notes.push(
        `読まなかったファイル (${res.files_skipped.length}): ` +
          res.files_skipped.slice(0, 20).join("、")
      );
    }
    refs.dropped.hidden = !notes.length;
    refs.dropped.textContent = notes.join(" / ");
  };

  /** 選ばれた関係をまとめて書き込む。1 本ずつ送ると同じエントリで上書きが起きる。 */
  const runApply = async () => {
    const targets = selected();
    if (!targets.length) return;
    busy = true;
    paintGo();
    setStatus(refs.status, `${targets.length} 本を書き込み中`, "busy");
    try {
      const res = await api("/api/relations", {
        method: "POST",
        body: {
          relations: targets.map((r) => ({
            from_ref: r.rel.from_ref,
            // 解決済みの ref で送る（同名がカテゴリ違いで併存しても取り違えない）
            to: r.rel.to_ref,
            label: r.rel.label,
            back: r.rel.back,
            rank: r.rel.rank,
            reveal: r.rel.reveal,
          })),
        },
      });
      applied += res.applied;
      const failed = res.results.filter((x) => !x.ok);
      for (const row of targets) {
        row.saved = true;
        row.check.checked = false;
        row.check.disabled = true;
        setStatus(row.state, "書き込みました");
      }
      for (const bad of failed) setStatus(refs.status, bad.detail, "error");
      if (!failed.length) setStatus(refs.status, `${res.applied} 本を書き込みました`);
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    busy = false;
    paintGo();
  };

  const onGo = () => (rows.length ? runApply() : runDraft());
  const onToggle = () => {
    const turnOn = selected().length === 0;
    for (const r of rows) if (!r.saved) r.check.checked = turnOn;
    paintGo();
  };
  const onStop = () => {
    aborted = true;
    controller?.abort();
    setStatus(refs.status, "中止しました", "error");
  };
  const finish = () => dialog.close();
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

  // 下書きは数十秒かかり、その間に閉じられる。listener は await の前に付ける
  // （extract.js と同じ理由。あとで付けると close を取り逃がして解決しない）
  const done = new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        aborted = true;
        controller?.abort();
        refs.spoiler.disabled = false;
        cleanup();
        resolve(applied);
      },
      { once: true }
    );
  });

  paintGo();
  return done;
}
