// 設定（データの保存先・表示テーマ・更新の確認）。全ページの topbar に差し込む。
//
// 既定ではデータがアプリの隣にあるので、更新のたびに手で data\ と content\ を
// コピーすることになる（`data\window` を取りこぼすとお気に入りと設定が静かに消える）。
// アプリの外へ移しておけば、更新は**フォルダを入れ替えるだけ**で済む。
//
// 設定ファイルはアプリのフォルダの外（OS のユーザー領域）にある。中に置くと、
// アプリを丸ごと入れ替えたときに設定ごと消えて意味が無い。
import { api, applyTheme, currentTheme, el, setStatus } from "./base.js";
// 更新のお知らせも topbar に出す。script タグを増やさずに済ませる
import { lastResult, refreshUpdateNotice } from "./update.js";

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
          <p class="notice" data-ref="outside" hidden></p>
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
        <section class="entry-section">
          <h2>表示</h2>
          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-theme">テーマ</label>
            <select id="gp-theme" class="auto-width" data-ref="theme">
              <option value="system">OS の設定に合わせる</option>
              <option value="light">ライト</option>
              <option value="dark">ダーク</option>
            </select>
          </div>
          <p class="hint">この設定はブラウザごとに残ります（専用ウィンドウとふだんのブラウザは別勘定）。</p>
        </section>
        <section class="entry-section">
          <h2>更新の確認</h2>
          <label class="check">
            <input type="checkbox" data-ref="updateCheck">
            <span>
              新しい版が出ていないか GitHub に聞く
              <span class="hint">1 日に 1 回まで。このアプリが外へ通信するのはここだけです。
                切ると一切通信しません。</span>
            </span>
          </label>
          <p class="hint" data-ref="updateState"></p>
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

/** 保存先の外に出ているものを知らせる。複製に乗らないので黙らない。 */
function paintOutside(info) {
  const outside = info.outside || [];
  refs.outside.hidden = !outside.length;
  if (outside.length) {
    refs.outside.textContent =
      `環境変数で保存先の外に出ているものがあります（複製されません）: ${outside.join("、")}`;
  }
}

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

/** 更新の確認の状態を描く。切ってあるときは版だけ出す。 */
async function paintUpdate() {
  let info = lastResult();
  if (!info) {
    try {
      info = await api("/api/update");
    } catch {
      info = null;
    }
  }
  if (!info) {
    refs.updateCheck.checked = true;
    refs.updateState.textContent = "";
    return;
  }
  refs.updateCheck.checked = info.enabled;
  const bits = [`いま ${info.current} を使っています。`];
  if (!info.enabled) bits.push("確認していません。");
  else if (info.newer) bits.push(`${info.latest} が出ています。`);
  else if (info.latest) bits.push("最新です。");
  // 失敗は隠さない。ただし本体には関係が無いので淡く出すだけ
  else if (info.error) bits.push("いまは確認できませんでした。");
  refs.updateState.textContent = bits.join(" ");
}

export async function openSettingsDialog() {
  build();
  refs.result.hidden = true;
  refs.theme.value = currentTheme();      // ローカルの設定なので待たずに出せる
  setStatus(refs.status, "読み込み中", "busy");
  dialog.showModal();

  // **listener は await の前に付ける。** ダイアログは開いた瞬間から操作できるのに、
  // 読み込みを待ってから付けると、その間の操作が黙って無視される（テーマを選んでも
  // 何も起きなかった）。extract.js の close を取り逃がした件と同じ形
  let info = null;

  // テーマは押した瞬間に効かせる（保存ボタンは保存先だけの話）
  const onTheme = () => applyTheme(refs.theme.value);

  const onMode = () => syncMode();

  // 更新の確認は切り替えた時点で保存する（保存ボタンは保存先だけの話なので）
  const onUpdateToggle = async () => {
    refs.updateCheck.disabled = true;
    try {
      await api("/api/update", { method: "PUT", body: { enabled: refs.updateCheck.checked } });
      await refreshUpdateNotice({ force: refs.updateCheck.checked });
      await paintUpdate();
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.updateCheck.disabled = false;
  };

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
      paintOutside(res);
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
  refs.theme.addEventListener("change", onTheme);
  refs.updateCheck.addEventListener("change", onUpdateToggle);
  refs.path.addEventListener("input", onMode);
  refs.pick.addEventListener("click", onPick);
  refs.save.addEventListener("click", onSave);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  refs.form.addEventListener("submit", onSubmit);

  try {
    info = await api("/api/settings");
    paintPaths(info);
    paintMode(info);
    refs.where.textContent = `設定ファイル: ${info.settings_file}`;
    paintOutside(info);
    refs.locked.hidden = !info.env_locked;
    if (info.env_locked) {
      refs.locked.textContent =
        "環境変数 GLOSSPOP_DATA_ROOT が設定されているので、ここでの設定より優先されます。" +
        "変えるには環境変数を外してください。";
    }
    for (const node of [refs.modeApp, refs.modeCustom, refs.path, refs.pick, refs.copy, refs.save]) {
      node.disabled = info.env_locked;
    }
    await paintUpdate();
    setStatus(refs.status, "");
  } catch (err) {
    // 読めなくてもダイアログは閉じない（テーマだけは変えられる）
    setStatus(refs.status, err.message, "error");
  }

  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        refs.modeApp.removeEventListener("change", onMode);
        refs.modeCustom.removeEventListener("change", onMode);
        refs.theme.removeEventListener("change", onTheme);
        refs.updateCheck.removeEventListener("change", onUpdateToggle);
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

/**
 * topbar に「⚙ 設定」を差し込む。どのページからも開けるように。
 *
 * **右端の隅に小さく置かない。** 最初そうしたら、更新のお知らせと語数表示に
 * 挟まれて誰も気づかない大きさになった。ナビゲーションの並びの直後に、
 * 文字つきで置く（ページ移動ではないので、区切り線で少し離す）。
 */
function install() {
  const bar = document.querySelector(".topbar");
  if (!bar || bar.querySelector("#settings")) return;
  const button = el("button", {
    type: "button",
    id: "settings",
    class: "topbar-action",
    title: "設定（データの保存先・更新の確認）",
    onclick: () => openSettingsDialog(),
  }, [
    el("span", { class: "topbar-action-icon", "aria-hidden": "true", text: "⚙" }),
    el("span", { text: "設定" }),
  ]);
  const nav = bar.querySelector(".topnav");
  if (nav) nav.after(button);
  else bar.append(button);
}

install();
