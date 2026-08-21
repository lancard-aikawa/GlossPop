// 公開する（辞書を 1 枚のページとメタ画像にして、決めたフォルダへ書き出す）。
//
// **カードは押す前に見せる。** X に貼ったときに出るのはこの絵で、書き出したあとに
// 気に入らないと分かっても直す道が画面に無い。ついでに、ここで描いた SVG を
// そのまま PNG にするので、**見せた絵と書き出す絵が同じもの**になる（別々に
// 作ると「見た絵と貼った絵が違う」が起きる）。
//
// **PNG にできるのはブラウザだけ。** サーバに画像ライブラリは無い（足すと
// `glosspop.spec` とビルド確認が付いてくる）ので、ここで作ってバイト列を送る。
import { api, el, setStatus } from "./base.js";
import { drawCard } from "./card.js";
import { figureBytes } from "./graph-export.js";

let dialog = null;
let refs = {};

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2>公開する</h2>
        <div class="spacer"></div>
        <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <p class="hint">
          いまの辞書を <strong>1 枚のページ</strong>にして、決めたフォルダへ書き出します。
          下の絵が、X などに貼ったときに出る<strong>メタ画像</strong>です。
          <strong>commit と push はしません</strong> —— 書くだけです。
        </p>
        <div class="setting-row setting-row-plain">
          <label class="field-inline" for="gp-pub-name">公開するフォルダ名</label>
          <input id="gp-pub-name" type="text" data-ref="name">
        </div>
        <div class="publish-card" data-ref="preview"></div>
        <p class="hint" data-ref="drawn"></p>
        <p class="notice" data-ref="plan"></p>
        <p class="notice" data-ref="result" hidden></p>
      </div>
      <footer>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="cancel">閉じる</button>
        <button type="button" class="primary" data-ref="go" disabled>書き出す</button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

/** 下見をそのまま文にする。**上書きになるものは名前で出す。** */
function planText(info) {
  if (!info.ready || !info.plan) {
    return "書き出し先のフォルダが決まっていません。⚙ の「公開」で決めてください。";
  }
  const plan = info.plan;
  const changed = plan.files.filter((f) => f.overwrite).map((f) => f.name);
  const docs = info.documents
    ? `辞書の 1 枚と、本文 ${info.documents} 件（辞書リンクと吹き出しが効く形）を`
    : "辞書の 1 枚を";
  const lines = [`${plan.dir} に ${docs}書きます。`];
  if (changed.length) lines.push(`${changed.join(" と ")} は上書きになります。`);
  if (plan.url) lines.push(`公開後の URL: ${plan.url}`);
  lines.push(...(plan.warnings || []));
  return lines.join(" ");
}

/** カードを描いて、そのまま PNG のもとにする。**畳んだ語の数は画面に出す。** */
function drawPreview(card) {
  refs.preview.replaceChildren();
  const drawn = drawCard(card, { host: refs.preview });
  drawn.root.setAttribute("width", "100%");
  drawn.root.removeAttribute("height");
  refs.drawn.textContent = drawn.dropped
    ? `${card.total} 語のうち ${drawn.shown} 語を載せました（残りは「他 ${drawn.dropped} 語」）。`
    : `${card.total} 語すべてを載せました。`;
  return drawn;
}

/**
 * 公開のダイアログを開く。
 *
 * ``name`` を省くと、サーバが開いているフォルダの名前を使う。
 */
export async function openPublishDialog({ name = "" } = {}) {
  build();
  refs.result.hidden = true;
  refs.plan.textContent = "";
  refs.drawn.textContent = "";
  refs.preview.replaceChildren();
  refs.go.disabled = true;
  setStatus(refs.status, "読み込み中", "busy");
  dialog.showModal();

  // **listener は最初の await より前に付ける。** 開いた瞬間から操作できるのに、
  // 読み込みを待ってから付けると、その間の操作が黙って無視される
  let drawn = null;
  let info = null;

  const onGo = async () => {
    if (!drawn || !info?.ready) return;
    refs.go.disabled = true;
    setStatus(refs.status, "書き出し中", "busy");
    try {
      // 見せている絵をそのまま PNG にする（別に作り直さない）
      const blob = await figureBytes(drawn.root, drawn.box, { kind: "png" });
      // **`api()` は JSON にしてしまう**ので、バイト列は fetch で直に送る
      // （顔の差し替え `POST /api/persona` と同じ形）
      const sent = await fetch(
        `/api/publish/card?name=${encodeURIComponent(refs.name.value.trim() || info.name)}`,
        { method: "POST", headers: { "Content-Type": "image/png" }, body: blob }
      );
      const made = await sent.json();
      if (!sent.ok) throw new Error(made.detail || `${sent.status} ${sent.statusText}`);
      const site = await api("/api/publish", {
        method: "POST",
        body: { name: refs.name.value.trim() || info.name, card_stamp: made.stamp },
      });
      refs.result.hidden = false;
      refs.result.textContent = site.url
        ? `${site.dir} に書きました。公開後の URL: ${site.url}`
        : `${site.dir} に書きました。${(site.warnings || []).join(" ")}`;
      setStatus(refs.status, "書き出しました");
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.go.disabled = false;
  };

  const finish = () => dialog.close();
  refs.go.addEventListener("click", onGo);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  // Enter で勝手に送信させない（書き出しは押して起こす）
  const onSubmit = (ev) => ev.preventDefault();
  refs.form.addEventListener("submit", onSubmit);

  /** 名前を変えたら下見もカードも取り直す（**カードの足もとに名前が出る**）。 */
  const load = async (wanted = "") => {
    setStatus(refs.status, "読み込み中", "busy");
    try {
      const query = wanted ? `?name=${encodeURIComponent(wanted)}` : "";
      info = await api(`/api/publish${query}`);
      refs.name.value = info.name;
      drawn = drawPreview(info.card);
      refs.plan.textContent = planText(info);
      refs.go.disabled = !info.ready;
      setStatus(refs.status, "");
    } catch (err) {
      refs.go.disabled = true;
      setStatus(refs.status, err.message, "error");
    }
  };
  const onName = () => load(refs.name.value.trim());
  refs.name.addEventListener("change", onName);

  await load(name);

  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        refs.go.removeEventListener("click", onGo);
        refs.cancel.removeEventListener("click", finish);
        refs.close.removeEventListener("click", finish);
        refs.form.removeEventListener("submit", onSubmit);
        refs.name.removeEventListener("change", onName);
        resolve();
      },
      { once: true }
    );
  });
}
