// テーマから本文を 1 枚書き、その本文に**要るのに名前の付いていない語**を
// 一節ごと足して、辞書まで書き出す。
//
// **抽出とは向きが逆。** ✨ 用語を抽出 は「すでにある文書に出てくる語」を挙げる。
// こちらは本文を書いてから、**本文が前提にしているのに名前の付いていない事柄**を
// 表に出す。だから提案は必ず **(語, その語を本文に出す一節)** の対で受け取る ——
// 本文に出てこない表記は、登録してもリンクにならない。
//
// **押した人の期待どおり、辞書に入るところまでやる。** 一節を入れて終わりにすると、
// 「書き出す」と書いてあるのに何も書き出されていないことになる。下書きと保存は
// `extract.js` と同じ口（`/api/ai/draft` → `/api/entries`）を叩く。
//
// **「書き出す」が 2 つ並ばないように、名前で対象を言う。**
// 「📄 本文を保存…」はファイル、「✨ 用語を辞書に書き出す」は辞書。
//
// **指定するのは 3 つだけ**（ジャンル / テーマ・仮定の論 / 語数）。
// **文体の欄は作らない** —— `.glosspop/style.md` と ⚙ が唯一の口で、
// 写しを作ると「フォルダに書いたのに効かない」が起きる。
//
// **本文欄は最初から出す。** 手持ちの本文を貼って、用語の書き出しだけ使う道を塞がない。
import { api, el, setStatus } from "./base.js";
import { invalidatePopupCache } from "./popup.js";

let dialog = null;
let refs = {};

const SIZE_KEY = "glosspop.compose.size";

/** 前回選んだ語数。読めない値はサーバの既定に落とす（覚えている値の検証）。 */
function rememberedSize() {
  try {
    const n = Number(localStorage.getItem(SIZE_KEY));
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function rememberSize(n) {
  try {
    localStorage.setItem(SIZE_KEY, String(n));
  } catch {
    /* localStorage が使えなくても止めない */
  }
}

function build() {
  if (dialog) return;
  dialog = el("dialog", { class: "sheet roomy", "data-ref": "composeDialog" });
  dialog.innerHTML = `
  <form method="dialog">
    <header>
      <h2 data-ref="title">AI 執筆</h2>
      <button type="button" class="ghost icon" data-ref="close" aria-label="閉じる">✕</button>
    </header>
    <div class="body">
      <p class="hint" data-ref="lead"></p>

      <section data-ref="askBox">
        <div class="setting-row">
          <label for="composeGenre">ジャンル</label>
          <select id="composeGenre" data-ref="genre" class="auto-width"></select>
          <label for="composeSize">語数</label>
          <select id="composeSize" data-ref="size" class="auto-width"></select>
          <button type="button" data-ref="write">✍ 本文を書く</button>
        </div>
        <label for="composeTheme">テーマ、または仮定の論</label>
        <textarea id="composeTheme" data-ref="theme" rows="2"
                  placeholder="空にすると、ジャンルもテーマも AI が選びます"></textarea>
        <p class="hint" data-ref="sizeNote"></p>
      </section>

      <section data-ref="textBox">
        <div class="compose-split">
          <div>
            <div class="check-row">
              <label for="composeText" class="wide">本文（貼り付けても、直しても構いません）</label>
              <button type="button" class="ghost" data-ref="copy"
                      title="本文の全文をクリップボードへ写します">📋 本文をコピー</button>
            </div>
            <textarea id="composeText" data-ref="text" rows="20"
                      placeholder="ここに本文が入ります。手持ちの文章を貼っても構いません"></textarea>
            <p class="hint" data-ref="textNote"></p>
          </div>
          <div>
            <label>プレビュー</label>
            <div class="compose-preview doc" data-ref="preview"></div>
            <p class="hint">登録済みの語はここでもリンクになります。</p>
          </div>
        </div>
      </section>

      <section data-ref="saveBox" hidden>
        <h3 class="rel-sub">本文をファイルにする</h3>
        <div class="setting-row">
          <label for="composeName">ファイル名</label>
          <input id="composeName" data-ref="name" type="text" class="wide">
        </div>
        <p class="hint" data-ref="target"></p>
      </section>

      <section data-ref="candBox" hidden>
        <h3 class="rel-sub">辞書に書き出す用語</h3>
        <p class="hint">
          チェックした語について、<strong>一節を本文に入れてから、辞書に登録します</strong>。
          本文の筋は変えません。
        </p>
        <div data-ref="list" class="filelist"></div>
        <details data-ref="droppedBox" hidden>
          <summary>出せなかったもの</summary>
          <div data-ref="dropped" class="hint"></div>
        </details>
      </section>

      <p class="status" data-ref="status" role="status"></p>
    </div>
    <footer>
      <button type="button" class="ghost push" data-ref="done">閉じる</button>
      <button type="button" class="ghost" data-ref="save" hidden>📄 本文を保存…</button>
      <button type="button" class="ghost" data-ref="open" hidden>ビューアで開く</button>
      <button type="button" class="primary" data-ref="go">✨ 用語を辞書に書き出す</button>
    </footer>
  </form>`;
  document.body.append(dialog);
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
}

/** 候補 1 件の行。**一節をそのまま見せる** —— 何が本文に入るのかを隠さない。 */
function makeRow(item) {
  const check = el("input", { type: "checkbox", checked: true });
  const where = item.anchor ? `「${item.anchor}」の節へ` : "本文の末尾へ";
  const state = el("span", { class: "status" });
  const row = el("label", { class: "check-row" }, [
    check,
    el("span", { class: "wide" }, [
      el("strong", { text: item.term }),
      el("span", { class: "rel-sub", text: ` ${item.kind_label || ""}` }),
      state,
      el("div", { class: "hint", text: `${where}${item.why ? " — " + item.why : ""}` }),
      el("blockquote", { class: "hint", text: item.passage }),
    ]),
  ]);
  return { item, check, state, row, saved: false };
}

export async function openComposeDialog({ onOpen = null, text = "" } = {}) {
  build();

  let rows = [];
  let busy = false;
  let body = "";
  //: 保存済みのファイル名。**開くときの行き先が変わる** —— 保存してあれば
  //: フォルダの中のファイルとして開ける（読書位置も `?doc=` の図も効く）
  let savedName = "";
  let sizes = [];
  let previewTimer = null;
  let previewSeq = 0;
  let targetTimer = null;

  const picked = () => rows.filter((r) => r.check.checked && !r.saved);

  const paint = () => {
    const has = Boolean(body.trim());
    refs.copy.disabled = !has;
    refs.saveBox.hidden = refs.save.hidden = !has;
    refs.open.hidden = !has || !onOpen;
    refs.open.textContent = savedName ? "ビューアで開く（保存したもの）" : "ビューアで開く";
    refs.open.title = savedName
      ? "フォルダの中のファイルとして開きます"
      : "保存せずにそのまま表示します —— 読書位置と、この文書だけの相関図・時系列は効きません";
    refs.write.textContent = has ? "✍ 書き直す" : "✍ 本文を書く";
    refs.write.disabled = busy;
    refs.save.disabled = busy;

    refs.go.disabled = busy || !has;
    if (rows.length) {
      const n = picked().length;
      refs.go.textContent = busy ? "書き出しています…" : `選んだ ${n} 語を書き出す`;
      refs.go.disabled = busy || n === 0;
    } else {
      refs.go.textContent = busy ? "待っています…" : "✨ 用語を辞書に書き出す";
    }
  };

  const setBody = (next, { rename = false, typing = false } = {}) => {
    body = next || "";
    if (!typing) refs.text.value = body;   // 打鍵中に書き戻すと選択位置が飛ぶ
    refs.textNote.textContent = `${body.length} 字`;
    if (rename) refs.name.value = "";      // 書き直したら題から付け直す
    savedName = "";                        // 直したものはまだファイルに入っていない
    rows = [];                             // 本文が変われば候補も作り直し
    refs.candBox.hidden = true;
    paint();
    // **打鍵のたびには引きに行かないが、blur を待たせもしない。**
    // 待たせると「どこへ保存されるのか」の説明が出ないまま欄だけが現れる
    scheduleTarget(typing ? 600 : 0);
    schedulePreview(typing ? 400 : 0);
  };

  const scheduleTarget = (delay = 600) => {
    clearTimeout(targetTimer);
    if (!body.trim()) return;
    targetTimer = setTimeout(refreshTarget, delay);
  };

  /** **どこへ書くかを必ず画面に出す。** 上書きになるかも押す前に見せる ——
   *  文書には控えの仕組みが無いので、先に見せることでしか担保できない。 */
  async function refreshTarget() {
    try {
      const info = await api(
        `/api/compose/target?name=${encodeURIComponent(refs.name.value || suggestFrom(body))}`,
      );
      if (!refs.name.value) refs.name.value = info.name;
      refs.target.textContent =
        `📁 ${info.root} に「${info.name}」として保存します。` +
        (info.exists ? "  ⚠ 同じ名前のファイルがすでにあります（保存すると上書きになります）。" : "");
      refs.target.className = info.exists ? "hint warn" : "hint";
    } catch {
      refs.target.textContent = "";
    }
  }

  /** **描くのはサーバ。** Markdown の解釈も辞書のリンク差し込みも `/api/render` が
   *  持っている —— こちらで別に描くと**ビューアと違う見え方**になる。 */
  async function renderPreview() {
    if (!body.trim()) {
      refs.preview.replaceChildren();
      return;
    }
    const seq = ++previewSeq;
    try {
      const res = await api("/api/render", {
        method: "POST",
        body: { text: body, kind: "markdown" },
      });
      if (seq !== previewSeq) return;   // 遅れて届いた古い応答で上書きしない
      refs.preview.innerHTML = res.html;
    } catch {
      if (seq === previewSeq) refs.preview.textContent = "（プレビューを描けませんでした）";
    }
  }

  const schedulePreview = (delay = 400) => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, delay);
  };

  /** 本文の題（最初の見出し行）。初期値のためだけ（正はサーバの `suggest_filename`）。 */
  const suggestFrom = (t) => (t.match(/^#\s+(.+?)\s*$/m) || [, ""])[1];

  const paintSizeNote = () => {
    const found = sizes.find((s) => s.size === Number(refs.size.value));
    refs.sizeNote.textContent = found
      ? `本文は ${found.chars} 字前後になります。足りなければ書き直せます。`
      : "";
  };

  // **listener は最初の await より前に付ける。** ダイアログは開いた瞬間から
  // 押せるので、読み込みを待ってから付けると、その間の操作が黙って無視される
  const finish = () => dialog.close();
  refs.close.addEventListener("click", finish);
  refs.done.addEventListener("click", finish);
  dialog.addEventListener(
    "close",
    () => {
      clearTimeout(previewTimer);
      clearTimeout(targetTimer);
      previewSeq++;                 // 閉じたあとに届いた描画で触らない
    },
    { once: true },
  );
  refs.text.addEventListener("input", () => setBody(refs.text.value, { typing: true }));
  refs.name.addEventListener("change", () => refreshTarget());
  refs.size.addEventListener("change", () => {
    rememberSize(Number(refs.size.value));
    paintSizeNote();
  });
  refs.copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(body);
      setStatus(refs.status, "本文の全文をクリップボードへ写しました");
    } catch {
      setStatus(refs.status, "コピーできませんでした（手で選んでください）", "error");
    }
  });
  refs.open.addEventListener("click", () => {
    if (!onOpen) return;
    // 保存してあればフォルダの中のファイルとして、まだなら貼り付けと同じ経路で
    onOpen(body, savedName);
    dialog.close();
  });
  refs.write.addEventListener("click", () => compose());
  refs.save.addEventListener("click", () => save());
  refs.go.addEventListener("click", () => (rows.length ? writeOut() : propose()));

  async function compose() {
    busy = true;
    setStatus(refs.status, "本文を書いています… （語数が多いほど待ちます）");
    paint();
    try {
      const res = await api("/api/ai/compose", {
        method: "POST",
        body: {
          genre: refs.genre.value,
          theme: refs.theme.value,
          size: Number(refs.size.value),
        },
      });
      setBody(res.text, { rename: true });
      setStatus(
        refs.status,
        `${res.chars} 字（目安 ${res.target_chars} 字）で書けました。そのまま直せます。`,
      );
    } catch (err) {
      setStatus(refs.status, String(err.message || err), "error");
    } finally {
      busy = false;
      paint();
    }
  }

  async function propose() {
    busy = true;
    setStatus(refs.status, "本文に要る語を探しています…");
    paint();
    try {
      const res = await api("/api/ai/needed", {
        method: "POST",
        body: { text: body, limit: Number(refs.size.value) },
      });
      rows = (res.candidates || []).map(makeRow);
      for (const r of rows) r.check.addEventListener("change", paint);
      refs.list.replaceChildren(...rows.map((r) => r.row));
      refs.candBox.hidden = false;
      // **落としたものは理由つきで出す**（黙って消さない）
      const dropped = res.dropped || [];
      refs.droppedBox.hidden = !dropped.length;
      refs.dropped.replaceChildren(
        ...dropped.map((d) => el("div", { text: `${d.term || "(空)"} — ${d.reason}` })),
      );
      setStatus(
        refs.status,
        rows.length
          ? `${rows.length} 語。チェックしたものを本文に足してから、辞書に登録します。`
          : "足せる語は見つかりませんでした。",
      );
    } catch (err) {
      setStatus(refs.status, String(err.message || err), "error");
    } finally {
      busy = false;
      paint();
    }
  }

  /**
   * 一節を本文に入れてから、**辞書に登録するところまで**やる。
   *
   * 「書き出す」と書いてあるのに何も書き出されていない、を避けるため。
   * 下書きは語ごとに数十秒かかるので、進み具合を 1 語ずつ出す。
   */
  async function writeOut() {
    const targets = picked();
    if (!targets.length) return;
    busy = true;
    paint();
    try {
      // 1. 本文に一節を入れる（入れ方の規則はサーバに 1 か所だけ置く）
      const res = await api("/api/ai/insert", {
        method: "POST",
        body: { text: body, items: targets.map((r) => r.item) },
      });
      body = res.text;
      refs.text.value = body;
      refs.textNote.textContent = `${body.length} 字`;
      savedName = "";               // 本文が変わったので、ファイルとは別物になった
      schedulePreview(0);

      // 2. 語ごとに下書きして保存する（`extract.js` と同じ口を叩く）
      let saved = 0;
      for (const [i, row] of targets.entries()) {
        setStatus(refs.status, `${i + 1}/${targets.length}: ${row.item.term} を下書き中…`, "busy");
        setStatus(row.state, "下書き中", "busy");
        try {
          const draft = await api("/api/ai/draft", {
            method: "POST",
            body: {
              term: row.item.term,
              context: row.item.passage,
              scope: "auto",
              kind: row.item.kind || "",
            },
          });
          const entry = await api("/api/entries", {
            method: "POST",
            body: { ...draft.draft, scope: draft.draft.scope || "global" },
          });
          row.saved = true;
          row.check.checked = false;
          row.check.disabled = true;
          setStatus(row.state, `保存しました (${entry.path_label})`);
          saved++;
        } catch (err) {
          setStatus(row.state, String(err.message || err), "error");
        }
      }
      // 辞書が変わったので、本文の吹き出しとリンクを作り直させる
      if (saved) invalidatePopupCache();
      setStatus(
        refs.status,
        `${targets.length} 語ぶんの一節を本文に入れ、${saved} 語を辞書に登録しました。` +
          "  本文はまだファイルになっていません（📄 本文を保存…）。",
      );
      refreshTarget();
      schedulePreview(0);           // 登録した語がプレビューでもリンクになる
    } catch (err) {
      setStatus(refs.status, String(err.message || err), "error");
    } finally {
      busy = false;
      paint();
    }
  }

  async function save({ overwrite = false } = {}) {
    busy = true;
    paint();
    try {
      const res = await api("/api/compose/save", {
        method: "POST",
        body: { text: body, name: refs.name.value, overwrite },
      });
      savedName = res.name;
      setStatus(
        refs.status,
        `${res.overwritten ? "上書き保存" : "保存"}しました: ${res.root} / ${res.name}。` +
          "これで読書位置も、この文書だけの相関図も効きます。",
      );
      refreshTarget();
      paint();
    } catch (err) {
      const message = String(err.message || err);
      // **黙って上書きしない。** 同名だったときだけ、もう一押しで上書きできるようにする
      if (/すでにあります/.test(message)) {
        setStatus(refs.status, message + "（もう一度押すと上書きします）", "error");
        refs.save.textContent = "📄 上書きして保存";
        refs.save.onclick = () => {
          refs.save.textContent = "📄 本文を保存…";
          refs.save.onclick = null;
          save({ overwrite: true });
        };
      } else {
        setStatus(refs.status, message, "error");
      }
    } finally {
      busy = false;
      paint();
    }
  }

  // 開いた時点で持っているものを描き、読み込みが届いたらもう一度描く。
  // **先に開くだけにすると、読み込みの前に開かれたときに空のまま固まる**
  refs.lead.textContent =
    "テーマから本文を書き、その本文に要る用語を辞書へ書き出します。" +
    "手持ちの本文を貼って、用語の書き出しだけ使うこともできます。";
  refs.theme.value = "";
  refs.name.value = "";
  refs.candBox.hidden = true;
  refs.droppedBox.hidden = true;
  refs.preview.replaceChildren();
  setStatus(refs.status, "");
  setBody(text || "");
  dialog.showModal();

  try {
    const opts = await api("/api/ai/compose-options");
    sizes = opts.sizes || [];
    refs.genre.replaceChildren(
      el("option", { value: "", text: "おまかせ" }),
      ...(opts.genres || []).map((g) =>
        el("option", { value: g.label, text: g.label, title: g.hint }),
      ),
    );
    const want = rememberedSize();
    refs.size.replaceChildren(
      ...sizes.map((s) => el("option", { value: String(s.size), text: `${s.size} 語` })),
    );
    refs.size.value = String(sizes.some((s) => s.size === want) ? want : opts.default_size);
    paintSizeNote();
  } catch (err) {
    setStatus(refs.status, String(err.message || err), "error");
  }
  paint();
}
