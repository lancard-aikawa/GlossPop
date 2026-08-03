// 設定 — いまはデータの保存先だけ。全ページの topbar に ⚙ を差し込む。
//
// 既定ではデータがアプリの隣にあるので、更新のたびに手で data\ と content\ を
// コピーすることになる（`data\window` を取りこぼすとお気に入りと設定が静かに消える）。
// アプリの外へ移しておけば、更新は**フォルダを入れ替えるだけ**で済む。
//
// 設定ファイルはアプリのフォルダの外（OS のユーザー領域）にある。中に置くと、
// アプリを丸ごと入れ替えたときに設定ごと消えて意味が無い。
import { api, el, setStatus } from "./base.js";

let dialog = null;
let refs = {};

function build() {
  if (dialog) return dialog;
  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2>設定</h2>
        <div class="spacer"></div>
        <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <section class="entry-section">
          <h2>データの保存先</h2>
          <p class="hint">
            辞書・カテゴリ・URL 辞書・読む文書・専用ウィンドウの設定は、すべてこの下にあります。
          </p>
          <p class="notice" data-ref="locked" hidden></p>
          <div class="setting-choice">
            <label class="check">
              <input type="radio" name="gp-data-root" value="app" data-ref="modeApp">
              <span>
                <strong>アプリの隣に置く</strong>
                <span class="hint">フォルダごと持ち運べる（USB でも動く）。
                  更新のたびに <code>data\\</code> と <code>content\\</code> を手でコピーする必要があります。</span>
              </span>
            </label>
            <label class="check">
              <input type="radio" name="gp-data-root" value="custom" data-ref="modeCustom">
              <span>
                <strong>別の場所に置く</strong>
                <span class="hint">アプリのフォルダを入れ替えるだけで更新できます。
                  データを手でコピーする必要がなくなります。</span>
              </span>
            </label>
          </div>
          <div class="setting-row" data-ref="pathRow">
            <input type="text" data-ref="path" placeholder="例: C:\\Users\\you\\Documents\\GlossPop"
                   aria-label="データの保存先">
            <button type="button" data-ref="pick">📁 選ぶ…</button>
          </div>
          <label class="check" data-ref="copyRow">
            <input type="checkbox" data-ref="copy" checked>
            <span>いまのデータを新しい場所へ複製する<span class="hint">（元は消しません）</span></span>
          </label>
          <details class="path-details" open>
            <summary>いま何がどこにあるか</summary>
            <dl class="path-list" data-ref="paths"></dl>
            <p class="hint" data-ref="where"></p>
          </details>
        </section>
        <p class="notice" data-ref="result" hidden></p>
      </div>
      <footer>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="cancel">閉じる</button>
        <button type="button" class="primary" data-ref="save">保存</button>
      </footer>
    </form>`;
  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

const PATH_LABELS = {
  glossary: "全体の辞書",
  categories: "カテゴリマスター",
  sites: "URL ごとの辞書",
  content: "読む文書（既定のフォルダ）",
  window_profile: "専用ウィンドウの設定・お気に入り",
};

function paintPaths(info) {
  refs.paths.replaceChildren(
    ...Object.entries(PATH_LABELS).flatMap(([key, label]) => [
      el("dt", { text: label }),
      el("dd", { text: info.paths[key] }),
    ])
  );
}

/** 現在の保存先。複製が要るかの判定に使う。 */
let currentRoot = "";

function paintMode(info) {
  currentRoot = info.data_root;
  const custom = !info.portable;
  refs.modeApp.checked = !custom;
  refs.modeCustom.checked = custom;
  refs.path.value = custom ? info.data_root : "";
  syncMode();
}

function syncMode() {
  const custom = refs.modeCustom.checked;
  refs.pathRow.hidden = !custom;
  // 保存先が変わらないなら複製は意味が無い。出しっぱなしにすると誤解を招く
  const target = custom ? refs.path.value.trim() : "";
  refs.copyRow.hidden = !target || target === currentRoot;
}

export async function openSettingsDialog() {
  build();
  refs.result.hidden = true;
  setStatus(refs.status, "読み込み中", "busy");
  dialog.showModal();

  let info = null;
  try {
    info = await api("/api/settings");
  } catch (err) {
    setStatus(refs.status, err.message, "error");
    return new Promise((resolve) => dialog.addEventListener("close", () => resolve(false), { once: true }));
  }

  paintPaths(info);
  paintMode(info);
  refs.where.textContent = `設定ファイル: ${info.settings_file}`;
  refs.locked.hidden = !info.env_locked;
  if (info.env_locked) {
    refs.locked.textContent =
      "環境変数 GLOSSPOP_DATA_ROOT が設定されているので、ここでの設定より優先されます。" +
      "変えるには環境変数を外してください。";
  }
  for (const node of [refs.modeApp, refs.modeCustom, refs.path, refs.pick, refs.copy, refs.save]) {
    node.disabled = info.env_locked;
  }
  setStatus(refs.status, "");

  const onMode = () => syncMode();

  const onPick = async () => {
    refs.pick.disabled = true;
    try {
      const res = await api("/api/pick-folder", {
        method: "POST",
        body: { initial: refs.path.value || info.data_root },
      });
      if (res.path) {
        refs.path.value = res.path;
        syncMode();
      }
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.pick.disabled = false;
  };

  const onSave = async () => {
    const custom = refs.modeCustom.checked;
    const path = refs.path.value.trim();
    if (custom && !path) {
      setStatus(refs.status, "保存先を入力してください", "error");
      refs.path.focus();
      return;
    }
    refs.save.disabled = true;
    setStatus(refs.status, "保存中", "busy");
    try {
      const res = await api("/api/settings", {
        method: "PUT",
        body: { data_root: custom ? path : "", copy_existing: refs.copy.checked },
      });
      paintPaths(res);
      refs.where.textContent = `設定ファイル: ${res.settings_file}`;
      const lines = [`次の起動から「${res.pending_data_root}」を使います。`];
      if (res.copy) {
        const cache = res.copy.cache_skipped
          ? `（キャッシュ ${res.copy.cache_skipped} 件は運びません。作り直されます）`
          : "";
        lines.push(
          `${res.copy.copied.length} 件を複製しました${cache}。元のデータは残っています。`
        );
        // 掴まれていて読めなかったものは黙って落とさない
        if (res.copy.skipped.length) {
          lines.push(
            `複製できなかったもの (${res.copy.skipped.length}): ` +
              res.copy.skipped.slice(0, 10).map((s) => `${s.path}（${s.reason}）`).join("、")
          );
        }
      }
      lines.push("GlossPop を一度終了して、開き直してください。");
      refs.result.hidden = false;
      refs.result.textContent = lines.join(" ");
      setStatus(refs.status, "保存しました（再起動が必要です）");
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.save.disabled = false;
  };

  const finish = () => dialog.close();
  const onSubmit = (ev) => ev.preventDefault();
  refs.modeApp.addEventListener("change", onMode);
  refs.modeCustom.addEventListener("change", onMode);
  refs.path.addEventListener("input", onMode);
  refs.pick.addEventListener("click", onPick);
  refs.save.addEventListener("click", onSave);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  refs.form.addEventListener("submit", onSubmit);

  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        refs.modeApp.removeEventListener("change", onMode);
        refs.modeCustom.removeEventListener("change", onMode);
        refs.path.removeEventListener("input", onMode);
        refs.pick.removeEventListener("click", onPick);
        refs.save.removeEventListener("click", onSave);
        refs.cancel.removeEventListener("click", finish);
        refs.close.removeEventListener("click", finish);
        refs.form.removeEventListener("submit", onSubmit);
        resolve(true);
      },
      { once: true }
    );
  });
}

/** topbar に ⚙ を差し込む。どのページからも開けるように。 */
function install() {
  const bar = document.querySelector(".topbar");
  if (!bar || bar.querySelector("#settings")) return;
  const button = el("button", {
    type: "button",
    id: "settings",
    class: "ghost",
    title: "設定（データの保存先）",
    "aria-label": "設定",
    text: "⚙",
    onclick: () => openSettingsDialog(),
  });
  // 語数表示 (.meta) の手前に置く
  const meta = bar.querySelector(".meta");
  if (meta) bar.insertBefore(button, meta);
  else bar.append(button);
}

install();
