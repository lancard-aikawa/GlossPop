// 文体（口調）と語り手の顔の編集。**設定ダイアログとビューアのサイドバーで共用する。**
//
// 同じものを 2 か所に書くと、優先順の案内や上限の文言が片方だけ古くなる
// （「全体に書いたのに効かない」が見えなくなるのがいちばん困る）。中身の出どころは
// ここ 1 つで、置き場所（host）と出すスコープだけを呼ぶ側が決める。
//
// **文体と顔は基準が違う。** 文体は「いま読んでいるもの」、顔は「そのエントリが
// どの辞書にあるか」。ここで同じ枠に並べているのは**同じ場所に置くファイルだから**で、
// 揃えたわけではない（→ CLAUDE.md）。だから顔はスコープごとに 1 行ずつ出す。
import { api, el, setStatus } from "./base.js";

const SCOPE_LABELS = {
  global: "全体に（どのフォルダでも）",
  local: "📁 いま開いているものだけに",
};

const STYLE_WHERE = { env: "環境変数", folder: "📁 このフォルダ", settings: "全体" };

/**
 * 文体と顔の編集 UI を ``host`` の中に組み立てる。
 *
 * `scopes` に並べたぶんだけ選べる。`onChange(info)` は保存が通ったあとに
 * 呼ばれる —— 呼ぶ側が同じ応答で自分の画面を描き直せるように、
 * `/api/ai/settings` と同じ形をそのまま渡す。
 *
 * `showPaths` を偽にすると**置き場所のパスを出さない**（狭いところに出すとき）。
 * 消すのはパスだけで、**「親フォルダのものが効いている」「辞書がないので置けない」は
 * 残す** —— どちらもパスではなく事実で、黙ると「全体に書いたのに効かない」
 * 「押せないのはなぜ」が見えなくなる。
 *
 * **いまの呼び出しは ⚙ の AI タブ 1 つだけ**（全体と 📁 の両方・パスあり）。
 * ビューアのサイドバーにも 📁 だけを出していたが、左を**ファイル一覧だけ**に
 * したときに畳んだ —— 口が 1 つになったので、優先順の案内も ⚙ 側にしかない。
 *
 * 返すのは `{ paint(info), reload(), locked }`。`paint()` は環境変数で
 * 固定されているときの注意書きを返す（呼ぶ側がまとめて 1 行に出せるように）。
 */
export function mountStyleEditor(
  host,
  { scopes = ["global", "local"], onChange = null, showPaths = true } = {}
) {
  const multi = scopes.length > 1;
  host.replaceChildren();
  host.innerHTML = `
    <div class="setting-row setting-row-plain" data-sref="scopeRow"${multi ? "" : " hidden"}>
      <label class="field-inline" data-sref="scopeLabel">この指定を</label>
      <select class="auto-width" data-sref="scope"></select>
    </div>
    <div class="setting-row setting-row-plain">
      <textarea data-sref="style" rows="3"
                placeholder="例: 講談・軍記物のような語り口で。歯切れよく、体言止めを混ぜる。"
                aria-label="文体（口調）の指定"></textarea>
    </div>
    <div class="chips" data-sref="presets"></div>
    <p class="hint" data-sref="note"></p>
    <!-- パスは円記号で割れないので、そのままだと入れ物ごと横に伸びる（サイドバーに
         横スクロールが出た）。path-line がどこでも折り返す。
         **ここにバッククォートを書かないこと** —— テンプレート文字列の中なので、
         コメントの中でも文字列が切れて続きが式として読まれる -->
    <p class="hint path-line" data-sref="where"></p>
    <div class="setting-row setting-row-plain">
      <button type="button" data-sref="save">文体を保存</button>
      <span class="status" data-sref="status"></span>
    </div>
    <h3 class="rel-sub-plain">語り手の顔</h3>
    <p class="hint" data-sref="personaHint"></p>
    <div class="persona-list" data-sref="personas"></div>
    <!-- 顔の結果は顔の側に出す。文体の保存ボタンの隣に出すと、そちらを押した
         結果に見える（同じ枠に 2 つの操作が入っているので混ざりやすい） -->
    <p class="status" data-sref="faceStatus"></p>
    <input type="file" data-sref="file" hidden
           accept="image/png,image/jpeg,image/webp,image/gif">`;

  const refs = {};
  for (const node of host.querySelectorAll("[data-sref]")) refs[node.dataset.sref] = node;

  refs.scope.replaceChildren(
    ...scopes.map((id) => el("option", { value: id, text: SCOPE_LABELS[id] || id }))
  );

  // 置き場所の話は ⚙ 側だけ（狭いサイドバーでは 3 行を食う）
  refs.personaHint.textContent = showPaths
    ? "吹き出しと用語ページに出ます。置き場所は辞書と同じなので、フォルダごとコピーすれば"
      + "顔もついていきます（エクスプローラから直接置いても同じです）。"
    : "吹き出しと用語ページに出ます。";

  //: 打ちかけの文体。保存先を切り替えても捨てない（書き直させると使われなくなる）
  const draft = { global: "", local: "" };
  let scope = scopes[0];
  let picked = false;                     // 一度でも人が選んだか
  let latest = null;                      // 最後に描いた応答
  let uploading = "";                     // 顔を送っている最中のスコープ

  /** 応答から、いま選ばれているスコープの持ち物を取り出す。 */
  const personaOf = (id) => (latest?.personas || []).find((p) => p.scope === id) || {};

  function paint(info) {
    latest = info;
    draft.global = info.style_global || "";
    draft.local = info.style_folder || "";

    const canLocal = Boolean(info.style_folder_path);
    const localOption = refs.scope.querySelector("option[value='local']");
    if (localOption) localOption.disabled = !canLocal;
    if (!picked && multi) {
      // 効いているものを最初に見せる（探しに行かせない）
      scope = info.style_source === "folder" ? "local" : "global";
    }
    if (scope === "local" && !canLocal && multi) scope = "global";
    refs.scope.value = scope;
    refs.style.value = draft[scope] || "";

    // 押せばそのまま入力欄に入る。**例文はサーバから来る**（画面と AI の 2 か所に
    // 書き分けると、片方だけ直したときに例と実際の効き方がずれる）
    refs.presets.replaceChildren(
      ...(info.style_presets || []).map((p) =>
        el("button", {
          type: "button",
          class: "chip",
          text: p.label,
          title: p.value,
          onclick: () => {
            refs.style.value = p.value;
            refs.style.focus();
          },
        })
      )
    );

    const note = [];
    if (info.style) {
      const where = STYLE_WHERE[info.style_source] || "全体";
      note.push(`いま効いているのは「${where}」の指定です。`);
      // 優先順を黙らない。押しのけられた側に書いた人がいちばん困る
      if (info.style_source === "folder" && info.style_global) {
        note.push("全体にも指定がありますが、こちらが優先されます。");
      }
      // 📁 だけを出している画面では、全体の指定が効いていることが見えない
      if (!multi && info.style_source !== "folder") {
        note.push("📁 に書くとこちらが優先されます。");
      }
    } else {
      note.push("指定なし。いつもどおりの説明文で書きます。");
    }
    note.push(`${info.style_max} 字まで。`);
    const over = (draft[scope] || "").length - info.style_max;
    if (over > 0) note.push(`いまの指定は上限を ${over} 字超えていて、超えた分は使いません。`);
    refs.note.textContent = note.join(" ");

    const ancestor = info.style_folder_is_ancestor
      ? `親フォルダ「${info.style_folder_label}」のものを使っています。` : "";
    if (!canLocal) {
      refs.where.textContent =
        "いま読んでいるものには辞書がないので、📁 の指定は置けません"
        + "（URL を読んでいて、その辞書をまだ作っていないときです）。";
    } else if (showPaths) {
      refs.where.textContent = `📁 の置き場所: ${info.style_folder_path}`
        + (ancestor ? `（${ancestor.slice(0, -1)}）` : "");
    } else {
      // パスは出さないが、**遠い祖先のものが効いていることは黙らない**
      refs.where.textContent = ancestor;
    }
    refs.where.hidden = !refs.where.textContent;

    paintPersonas(info);

    // 環境変数で固定されているなら、押しても書き込めない（押せると嘘になる）
    const locked = info.style_source === "env";
    refs.style.disabled = locked;
    refs.scope.disabled = locked;
    refs.save.disabled = locked;
    for (const chip of refs.presets.children) chip.disabled = locked;
    return locked ? "文体は環境変数で固定されています。" : "";
  }

  /**
   * 顔をスコープごとに 1 行ずつ。**置き場所は画面から差し替えられても隠さない**
   * —— エディタやエクスプローラから置く道は残っているので、片方だけ知っていると
   * 「差し替えたのに変わらない」の原因（別の拡張子の顔が残っている等）が見えない。
   */
  function paintPersonas(info) {
    refs.personas.replaceChildren(
      ...scopes.map((id) => {
        const item = (info.personas || []).find((p) => p.scope === id) || { scope: id };
        const where = item.found ? item.path : item.dir;
        const canWrite = Boolean(item.dir);
        const face = item.found && item.url
          ? el("img", { class: "persona-face", src: item.url, alt: `${item.label} の顔` })
          : el("span", { class: "persona-face empty", "aria-hidden": "true", text: "🙂" });
        // パスを出さない画面でも**押せない理由は出す**（`title` にはパスを残す）
        const detail = !canWrite
          ? "未設定（いま読んでいるものに辞書がないので置けません）"
          : showPaths
            ? (item.found ? where : `未設定（${where} に置かれます）`)
            : (item.found ? "" : "未設定");
        return el("div", { class: "persona-row" }, [
          face,
          el("div", { class: "persona-main" }, [
            el("span", { class: "persona-label", text: item.label || id }),
            el("span", { class: "issue-detail", title: where || "", text: detail, hidden: !detail }),
          ]),
          el("button", {
            type: "button",
            text: item.found ? "差し替え…" : "選ぶ…",
            disabled: !canWrite || uploading === id,
            onclick: () => pickFace(id),
          }),
          el("button", {
            type: "button",
            class: "ghost",
            title: "顔を消す",
            "aria-label": `${item.label || id} の顔を消す`,
            text: "🗑",
            hidden: !item.found,
            onclick: () => dropFace(id),
          }),
        ]);
      })
    );
  }

  let picking = "";

  function pickFace(id) {
    picking = id;
    refs.file.click();
  }

  /**
   * 顔を送る。**ファイル名は送らない**（サーバは中身から拡張子を決める）ので、
   * multipart ではなく生のバイト列にしてある（取り込みの zip と同じ形）。
   */
  async function sendFace(id, file) {
    if (latest?.persona_max && file.size > latest.persona_max) {
      setStatus(refs.faceStatus, `画像は ${Math.round(latest.persona_max / 1024 / 1024)} MB までです`, "error");
      return;
    }
    uploading = id;
    setStatus(refs.faceStatus, "顔を送っています", "busy");
    try {
      const res = await fetch(`/api/persona?scope=${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
      uploading = "";
      paint(data);
      setStatus(refs.faceStatus, "顔を差し替えました");
      onChange?.(data);
    } catch (err) {
      uploading = "";
      setStatus(refs.faceStatus, err.message, "error");
      paintPersonas(latest || {});
    }
  }

  async function dropFace(id) {
    const item = personaOf(id);
    if (!confirm(`${item.label || id} の顔を消します。よろしいですか？`)) return;
    setStatus(refs.faceStatus, "消しています", "busy");
    try {
      const data = await api(`/api/persona?scope=${encodeURIComponent(id)}`, { method: "DELETE" });
      paint(data);
      setStatus(refs.faceStatus, "消しました");
      onChange?.(data);
    } catch (err) {
      setStatus(refs.faceStatus, err.message, "error");
    }
  }

  const onFile = (ev) => {
    const file = ev.target.files?.[0];
    ev.target.value = "";               // 同じファイルをもう一度選べるように
    if (file && picking) sendFace(picking, file);
    picking = "";
  };

  /**
   * 文体を保存する。**AI の選択とは別の口**（`/api/ai/style`）。
   *
   * 📁 を選ぶとフォルダに ``.glosspop/style.md`` を作りうるので、モデルを
   * 選び直したついでに書かれると「開いただけのフォルダを汚さない」が崩れる。
   */
  const onSave = async () => {
    refs.save.disabled = true;
    setStatus(refs.status, "保存中", "busy");
    try {
      const data = await api("/api/ai/style", {
        method: "PUT",
        body: { scope, style: refs.style.value.trim() },
      });
      paint(data);
      // 保存はできたが効かない、を黙らない（押しのけられた側に書いた人が困る）
      setStatus(refs.status,
        scope === "global" && data.style_source === "folder"
          ? "保存しました（いまは 📁 の指定が優先されています）"
          : "保存しました");
      onChange?.(data);
    } catch (err) {
      setStatus(refs.status, err.message, "error");
      refs.save.disabled = false;
    }
  };

  /** 保存先を切り替える。**打ちかけは捨てない**（書き直させると使われなくなる）。 */
  const onScope = () => {
    draft[scope] = refs.style.value;
    scope = refs.scope.value;
    picked = true;
    refs.style.value = draft[scope] || "";
  };

  refs.save.addEventListener("click", onSave);
  refs.scope.addEventListener("change", onScope);
  refs.file.addEventListener("change", onFile);

  return {
    paint,
    /** 効いているほうを次に開いたときもう一度先に見せる（ダイアログを開き直したとき）。 */
    resetScope() {
      picked = false;
    },
    async reload() {
      try {
        paint(await api("/api/ai/settings"));
      } catch (err) {
        setStatus(refs.status, err.message, "error");
      }
    },
  };
}
