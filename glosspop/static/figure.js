// 図を作る（本文が描写しているものを、図形と文字の 1 枚にして用語の画像にする）。
//
// **既存の図とは出どころが違う。** 相関図の 6 つも地図も年表もカードも、出どころは
// 構造化された項目（`relations` / `when` / `map_shape`）で決定的に描ける。ここが
// 描くのは**本文が描写しているのに、どの項目にも入っていないもの**なので、
// 出どころは自由文しかなく、毎回同じ絵にはならない。
//
// **押す前に必ず見せる。** 図が本文と合っているかを機械で確かめる手段はこちらに
// 無い（形式は `core.figuresvg` が削るが、絵の意味は照合できない）ので、担保は
// 「見せてから人が押す」しかない —— 公開カードを押す前に見せているのと同じ理由。
//
// **保存は PNG 1 枚、SVG は手元に落とす道だけ。** 置き場所は `<slug>.<拡張子>` で
// 1 鍵 1 枚と決まっていて、`store.clear_other_images()` が別拡張子を能動的に消す
// （残すと探索順で出る絵が決まり「差し替えたのに変わらない」になる）。SVG を辞書に
// 持っても開き直して直す口がアプリに無いので、**焼く先と持ち方を分ける理由が無い**。
// PNG 化は `graph-export.js` の道をそのまま通す（サーバに画像ライブラリは無い）。
import { api, el, setStatus, ticking } from "./base.js";
import { figureBytes, saveGraph } from "./graph-export.js";

let dialog = null;
let refs = {};

//: 枠の一覧。**サーバが正**（`ai.FIGURE_KINDS`）なので、ここに写しを置かない。
//: 1 回引いたら覚える（開き直すたびに引き直す理由が無い）
let kinds = null;

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2>図を作る</h2>
        <div class="spacer"></div>
        <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <p class="hint">
          この語の<strong>本文と要約</strong>から、図形と文字だけの図を 1 枚描きます。
          <strong>入れるまで辞書は変わりません</strong> —— 下に出た絵を見てから決めてください。
        </p>
        <div class="setting-row setting-row-plain">
          <label class="field-inline" for="gp-fig-kind">何の図か</label>
          <select id="gp-fig-kind" data-ref="kind"></select>
        </div>
        <p class="hint" data-ref="kindHint"></p>
        <div class="setting-row setting-row-plain">
          <label class="field-inline" for="gp-fig-note">補足（任意）</label>
          <input id="gp-fig-note" type="text" data-ref="note"
                 placeholder="例: 北を上にする / 3 つの層に分ける">
        </div>
        <div class="figure-preview" data-ref="preview" hidden></div>
        <p class="notice" data-ref="note2" hidden></p>
      </div>
      <footer>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="svg" hidden>⬇ SVG で保存</button>
        <button type="button" data-ref="draw">✨ 図を描く</button>
        <button type="button" class="primary" data-ref="put" disabled>この図を入れる</button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

/**
 * 削り終えた SVG を画面に置く。**`innerHTML` に流し込まない** —— HTML の
 * パーサに食わせると属性の大文字小文字（`viewBox`）が潰れて box が読めなくなる。
 * XML として読めば、**読めないものはここで分かる**（サーバが削った後なので
 * 普通は通るが、通らないものを黙って空の枠にしない）。
 */
function paint(svgText, box) {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  if (doc.querySelector("parsererror")) return null;
  const node = document.importNode(doc.documentElement, true);
  // **入れ物に合わせて出す。** `width` / `height` は焼くときに `box` から
  // 書き直されるので、画面の都合でここを潰してよい（`publish.js` と同じ）
  node.setAttribute("width", "100%");
  node.removeAttribute("height");
  refs.preview.replaceChildren(node);
  refs.preview.hidden = false;
  return { root: node, box: { x: box[0], y: box[1], w: box[2], h: box[3] } };
}

/** 落としたものと図形の数を 1 行にする。**黙って削った絵を出さない。** */
function drawnText(made) {
  const bits = [`図形 ${made.shapes} 個・文字 ${made.texts} 個`];
  if (made.dropped?.length) {
    bits.push(`受け取れない書き方を落としました（${made.dropped.join(" / ")}）`);
  }
  return bits.join("。");
}

/**
 * 図のダイアログを開く。``entry`` は用語ページが持っているもの（`ref` と `term`）。
 *
 * ``onSaved`` は入れ終わったあとに呼ぶ（一覧と吹き出しの作り直しは**呼ぶ側**の
 * 仕事 —— ここは辞書の状態を知らない）。
 */
export async function openFigureDialog(entry, { onSaved = null } = {}) {
  build();
  refs.preview.replaceChildren();
  refs.preview.hidden = true;
  refs.note2.hidden = true;
  refs.svg.hidden = true;
  refs.put.disabled = true;
  refs.note.value = "";
  setStatus(refs.status, "");

  // **listener は最初の await より前に付ける。** ダイアログは開いた瞬間から
  // 操作できるので、読み込みを待ってから付けるとその間の操作が黙って消える
  let drawn = null;

  const onDraw = async () => {
    refs.draw.disabled = true;
    refs.put.disabled = true;
    refs.svg.hidden = true;
    refs.note2.hidden = true;
    drawn = null;
    // **経過秒を打ち直す。** 応答は最後にまとめて返るので、出さないと固まって見える
    const tick = ticking(refs.status, (s) => `描いています… ${s} 秒`);
    try {
      const made = await api("/api/ai/figure", {
        method: "POST",
        body: { ref: entry.ref, kind: refs.kind.value, note: refs.note.value.trim() },
      });
      tick.stop();
      if (!made.svg) {
        // **描けなかったのは失敗ではない。** 「描けなければ描かない」と頼んで
        // あるので、そう返ってきたぶんはそのまま出す
        refs.preview.replaceChildren();
        refs.preview.hidden = true;
        refs.note2.hidden = false;
        refs.note2.textContent = made.why || "図を作れませんでした。";
        setStatus(refs.status, "");
      } else {
        drawn = paint(made.svg, made.box);
        if (!drawn) throw new Error("受け取った図を表示できませんでした");
        refs.note2.hidden = false;
        refs.note2.textContent = drawnText(made);
        refs.put.disabled = false;
        refs.svg.hidden = false;
        setStatus(refs.status, "");
      }
    } catch (err) {
      tick.stop();
      setStatus(refs.status, err.message, "error");
    }
    refs.draw.disabled = false;
  };

  const onPut = async () => {
    if (!drawn) return;
    refs.put.disabled = true;
    setStatus(refs.status, "入れています", "busy");
    try {
      // **見せている絵をそのまま焼く**（別に作り直さない。公開カードと同じ約束）
      const blob = await figureBytes(drawn.root, drawn.box, { kind: "png" });
      const res = await fetch(
        `/api/entry-image?ref=${encodeURIComponent(entry.ref)}`,
        { method: "POST", body: blob, headers: { "Content-Type": "application/octet-stream" } }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
      setStatus(refs.status, "入れました");
      if (onSaved) await onSaved();
      dialog.close();
    } catch (err) {
      setStatus(refs.status, err.message, "error");
      refs.put.disabled = false;
    }
  };

  //: **SVG は手元に落とすだけ。** 辞書には PNG しか置かないので、拡大したい・
  //: 外のエディタで直したいぶんはここから持ち出す（相関図の ⋯ と同じ 2 択）
  const onSvg = () => {
    if (drawn) saveGraph(drawn.root, drawn.box, { kind: "svg", name: entry.term || "図" });
  };
  const onClose = () => dialog.close();
  const onSubmit = (ev) => ev.preventDefault();      // Enter で勝手に描かせない
  const onKind = () => paintHint();

  refs.draw.addEventListener("click", onDraw);
  refs.put.addEventListener("click", onPut);
  refs.svg.addEventListener("click", onSvg);
  refs.close.addEventListener("click", onClose);
  refs.form.addEventListener("submit", onSubmit);
  refs.kind.addEventListener("change", onKind);
  dialog.showModal();

  function paintHint() {
    const found = (kinds?.kinds || []).find((k) => k.key === refs.kind.value);
    refs.kindHint.textContent = found ? found.hint : "";
  }

  // 開いた時点で持っているぶんを描き、届いたらもう一度描く（**開いてすぐ操作
  // されても空のまま固まらない**ように。カテゴリ管理で踏んだのと同じ形）
  const fill = () => {
    refs.kind.replaceChildren(...(kinds?.kinds || []).map(
      (k) => el("option", { value: k.key, text: k.label })
    ));
    if (kinds?.default) refs.kind.value = kinds.default;
    if (kinds?.note_max) refs.note.maxLength = kinds.note_max;
    paintHint();
  };
  fill();
  if (!kinds) {
    try {
      kinds = await api("/api/ai/figure-kinds");
      fill();
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
  }

  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      refs.draw.removeEventListener("click", onDraw);
      refs.put.removeEventListener("click", onPut);
      refs.svg.removeEventListener("click", onSvg);
      refs.close.removeEventListener("click", onClose);
      refs.form.removeEventListener("submit", onSubmit);
      refs.kind.removeEventListener("change", onKind);
      resolve();
    }, { once: true });
  });
}
