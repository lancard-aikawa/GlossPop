// 設定（データの保存先・AI・表示テーマ・更新の確認）。全ページの topbar に差し込む。
//
// 既定ではデータがアプリの隣にあるので、更新のたびに手で data\ と content\ を
// コピーすることになる（`data\window` を取りこぼすとお気に入りと設定が静かに消える）。
// アプリの外へ移しておけば、更新は**フォルダを入れ替えるだけ**で済む。
//
// 設定ファイルはアプリのフォルダの外（OS のユーザー領域）にある。中に置くと、
// アプリを丸ごと入れ替えたときに設定ごと消えて意味が無い。
import {
  api,
  applyFontSize,
  applyTheme,
  currentFontSize,
  currentTheme,
  el,
  firstOnly,
  paintEntryCount,
  setFirstOnly,
  setStatus,
} from "./base.js";
// 文体と顔の編集は**ビューアのサイドバーと共用**（写しを作らない）
import { mountStyleEditor } from "./ai-style.js";
// 更新のお知らせも topbar に出す。script タグを増やさずに済ませる
import { lastResult, refreshUpdateNotice } from "./update.js";

let dialog = null;
let refs = {};
let styleEditor = null;

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
      <div class="sheet-tabs" role="tablist" data-ref="tabs">
        <button type="button" role="tab" data-tab="general" aria-selected="true"
                aria-controls="gp-tab-general" id="gp-tab-general-btn">一般</button>
        <button type="button" role="tab" data-tab="ai" aria-selected="false"
                aria-controls="gp-tab-ai" id="gp-tab-ai-btn">AI<span class="tab-mark" data-ref="aiMark" hidden>●</span></button>
        <button type="button" role="tab" data-tab="publish" aria-selected="false"
                aria-controls="gp-tab-publish" id="gp-tab-publish-btn">公開</button>
      </div>
      <div class="body" data-panel="general" id="gp-tab-general"
           role="tabpanel" aria-labelledby="gp-tab-general-btn">
        <section class="entry-section" data-ref="importBox" hidden>
          <h2>隣のフォルダにデータがあります</h2>
          <p class="hint">
            新しい版を隣に展開して起動すると、辞書は前のフォルダに残ったままになります。
            ここから引き継げます（<strong>元は消しません</strong>）。
          </p>
          <ul class="rel-list" data-ref="imports"></ul>
        </section>
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
          <h2>辞書の書き出し / 取り込み</h2>
          <p class="hint">
            全体の辞書とカテゴリマスターを zip にします。中身は Markdown のままなので、
            解凍すればそのまま読めます。<strong>フォルダの辞書（<code>.glosspop</code>）と
            URL ごとの辞書は含みません。</strong>
          </p>
          <!-- 書き出しは 1 つの動作、取り込みは「どう取り込むか」を伴う動作。
               行を分けて、取り込み方はその押すボタンの隣に置く（前に置くと
               書き出しにも掛かって見える）。選択ボックスは中身に合わせて短く -->
          <!-- **渡す範囲は書き出す側で決める。** 取り込む側は変えていない
               （併合は入っているものを足して上書きするだけなので、中身が一部でも
               そのまま通る）。→ docs/design-notes.md -->
          <div class="setting-row setting-row-plain">
            <button type="button" data-ref="export">⬇ 書き出す</button>
            <label class="field-inline" for="gp-export-scope">範囲</label>
            <select id="gp-export-scope" class="auto-width" data-ref="exportScope">
              <option value="">辞書全体</option>
            </select>
          </div>
          <p class="hint" data-ref="exportNote"></p>
          <div class="setting-row setting-row-plain">
            <button type="button" data-ref="importPick">⬆ 取り込む…</button>
            <label class="field-inline" for="gp-import-mode">取り込み方</label>
            <select id="gp-import-mode" class="auto-width" data-ref="importMode">
              <option value="replace">置き換える</option>
              <option value="merge">併合する</option>
            </select>
            <input type="file" accept=".zip,application/zip" data-ref="importFile" hidden>
          </div>
          <!-- **控えの中を見る口。** 併合の衝突は「取り込む側が勝つ」なので、
               上書きされた語は控えにしか残らない。zip を手で開かせるのでは
               約束が半分しか果たせない（→ docs/design-notes.md） -->
          <details data-ref="backupBox">
            <summary>取り込み前の控え <span data-ref="backupCount"></span></summary>
            <p class="hint">
              取り込みの前に自動で取ったものです。中を見て<strong>1 件だけ戻す</strong>
              こともできます。<strong>古いものを自動で消すことはしません</strong>ので、
              溜まったらここで捨ててください。
            </p>
            <div class="filelist" data-ref="backupList"></div>
          </details>
          <p class="notice">
            <strong>置き換え</strong>は、いまの辞書が zip の中身に入れ替わり、zip に無い
            用語が消えます。<strong>併合</strong>は zip にしか無い用語を足し、
            <strong>両方にある用語は zip の側で上書き</strong>します（手元にしか無い用語は
            消えません）。どちらも実行の前に控えを自動で取るので、そこから戻せます。
            <strong>何が増えて・上書きされて・消えるかは、実行の前に件数で出ます。</strong>
          </p>
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
          <!-- 大きさは 1 つだけ選ばせる。ここが style.css の基準 (fs-base) を決め、
               注記も見出しも同じ比で付いてくる（**周りの px は触らない**）。
               バッククォートを書かないこと -- ここは JS のテンプレート文字列の中 -->

          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-fontsize">文字の大きさ</label>
            <select id="gp-fontsize" class="auto-width" data-ref="fontSize">
              <option value="small">小</option>
              <option value="medium">中（既定）</option>
              <option value="large">大</option>
              <option value="xlarge">特大</option>
            </select>
          </div>
          <p class="hint">本文も画面の文字もまとめて変わります。相関図の図の中だけは
            配置の計算に関わるので変わりません（図は拡大縮小で大きくできます）。</p>
          <!-- 本文の見せ方。**効くのはビューアだが置き場所はここ** ——
               サイドバーは「何を読むか」を選ぶ場所で、読み方の設定はここに集める -->
          <label class="check">
            <input type="checkbox" data-ref="firstOnly">
            <span>
              各用語の最初の 1 回だけリンクする
              <span class="hint">同じ語が何度も出てくる文書で、本文がリンクだらけになるのを防ぎます。</span>
            </span>
          </label>
          <p class="hint">これらの設定はブラウザごとに残ります（専用ウィンドウとふだんのブラウザは別勘定）。</p>
        </section>
        <section class="entry-section">
          <h2>更新の確認</h2>
          <label class="check">
            <input type="checkbox" data-ref="updateCheck">
            <span>
              新しい版が出ていないか GitHub に聞く
              <span class="hint">1 日に 1 回まで。切ると更新の確認では通信しません。
                （AI に <strong>Gemini API</strong> を選んだときは、下書きのたびに
                Google へ本文を送ります。Claude Code CLI を選んでいる場合は
                CLI 側が通信します。）</span>
            </span>
          </label>
          <p class="hint" data-ref="updateState"></p>
          <div class="setting-row setting-row-plain" data-ref="downloadRow" hidden>
            <button type="button" data-ref="download">⬇ 新しい版を隣に展開する</button>
            <span class="hint">
              いまのフォルダは触りません。展開したら、そちらを起動してください。
            </span>
          </div>
        </section>
        <p class="notice" data-ref="result" hidden></p>
      </div>
      <div class="body" data-panel="ai" id="gp-tab-ai"
           role="tabpanel" aria-labelledby="gp-tab-ai-btn" hidden>
        <section class="entry-section">
          <h2>AI（下書き・抽出・関係）</h2>
          <p class="hint">
            用語の下書き、候補の抽出、関係の下書きに使う AI です。
            <strong>速さと精度はモデルと思考の深さで大きく変わります。</strong>
            ここでの変更は<strong>次の呼び出しから効きます</strong>（再起動は要りません）。
          </p>
          <p class="notice" data-ref="aiLocked" hidden></p>
          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-ai-provider">使う AI</label>
            <select id="gp-ai-provider" class="auto-width" data-ref="aiProvider"></select>
          </div>
          <p class="hint" data-ref="aiHint"></p>
          <div class="setting-row" data-ref="aiKeyRow" hidden>
            <input type="password" data-ref="aiKey" autocomplete="off"
                   placeholder="Gemini API キー（AI Studio で発行）" aria-label="Gemini API キー">
            <button type="button" data-ref="aiKeyClear">登録を消す</button>
          </div>
          <div class="setting-row">
            <label class="field-inline" for="gp-ai-model">モデル</label>
            <input id="gp-ai-model" type="text" list="gp-ai-models" data-ref="aiModel"
                   placeholder="空なら既定" aria-label="モデル" autocomplete="off">
            <datalist id="gp-ai-models" data-ref="aiModelList"></datalist>
            <button type="button" data-ref="aiModelFetch">一覧を取り直す</button>
          </div>
          <p class="hint" data-ref="aiModelNote"></p>
          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-ai-effort">思考の深さ</label>
            <select id="gp-ai-effort" class="auto-width" data-ref="aiEffort"></select>
          </div>
          <p class="hint">
            深くするほど丁寧になり、そのぶん時間がかかります
            （所要時間は考えた量にほぼ比例します）。
          </p>
        </section>
          <div class="setting-row setting-row-plain">
            <button type="button" data-ref="aiSave">AI の設定を保存</button>
            <span class="status" data-ref="aiStatus"></span>
          </div>
        </section>
        <section class="entry-section">
          <h2>文体（口調）と語り手の顔</h2>
          <p class="hint">
            AI が書く文章の調子を指定できます（「講談調で」「TRPG のルールブック風に」など）。
            効くのは<strong>要約・本文・使用例と、関係の一言だけ</strong>です ——
            用語名・カテゴリ・関係の相手は、崩すと保存できなくなるので変わりません。
            空にして保存すると指定なしに戻ります。
          </p>
          <!-- 中身は ai-style.js が作る。**ここに写しを置かないこと** ——
               ビューアのサイドバーが同じものを 📁 だけに絞って出している -->
          <div data-ref="styleHost"></div>
        </section>
      </div>
      <div class="body" data-panel="publish" id="gp-tab-publish"
           role="tabpanel" aria-labelledby="gp-tab-publish-btn" hidden>
        <section class="entry-section">
          <h2>公開（GitHub Pages など）</h2>
          <p class="hint">
            辞書を<strong>1 枚のページ</strong>にして、指定したフォルダへ書き出します。
            X などに貼ったときに出る<strong>メタ画像</strong>も一緒に作ります。
            ここでの変更は<strong>その場で効きます</strong>（再起動は要りません）。
            <strong>commit と push はしません</strong> —— 書くだけです。
          </p>
          <p class="notice" data-ref="pubLocked" hidden></p>
          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-pub-dir">書き出し先のフォルダ</label>
            <input id="gp-pub-dir" type="text" data-ref="pubDir"
                   placeholder="例: C:\\Repos\\mysite\\htmlize">
          </div>
          <div class="setting-row setting-row-plain">
            <label class="field-inline" for="gp-pub-base">公開先の URL</label>
            <input id="gp-pub-base" type="text" data-ref="pubBase"
                   placeholder="https://ユーザ名.github.io/リポジトリ/">
          </div>
          <p class="hint">
            <strong>URL を書かないと、貼ったときにカードの画像が出ません。</strong>
            メタ画像の URL は絶対でないと無視されるためで、
            <strong>ページ自体は正しく出る</strong>ぶん気づきにくいところです。
          </p>
          <div class="setting-row setting-row-plain">
            <button type="button" data-ref="pubSave">公開の設定を保存</button>
            <span class="status" data-ref="pubStatus"></span>
          </div>
        </section>
        <section class="entry-section">
          <h2>いま書き出される場所</h2>
          <!-- **押す前にどこへ何が書かれるかを出す。** 取り込みの下見と同じ扱い -->
          <p class="hint" data-ref="pubPlan">まだ決まっていません。</p>
        </section>
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
  // **refs を集めたあとで差し込む。** 中の部品は `data-sref` を使っているので
  // ここで拾うことはないが、順番も逆にしないこと（同じ名前の save / status がある）
  styleEditor = mountStyleEditor(refs.styleHost, { scopes: ["global", "local"] });
  document.body.append(dialog);
  return dialog;
}

const PATH_LABELS = {
  glossary: "全体の辞書",
  categories: "カテゴリマスター",
  sites: "URL ごとの辞書",
  content: "読む文書（既定のフォルダ）",
  window_profile: "専用ウィンドウの設定・お気に入り",
  backups: "取り込み前の控え",
};

/** 隣に置き去りのデータを出す。更新で「辞書が消えた」ように見える状態の救済。 */
function paintImports(info, onImport) {
  const candidates = info.import_candidates || [];
  refs.importBox.hidden = !candidates.length;
  refs.imports.replaceChildren(
    ...candidates.map((c) =>
      el("li", { class: "rel-row" }, [
        el("span", { class: "rel-label", text: `${c.name}（${c.entry_count} 語）` }),
        el("span", { class: "issue-detail", text: c.path }),
        el("span", { class: "spacer" }),
        el("button", {
          type: "button",
          class: "primary",
          text: "引き継ぐ",
          onclick: () => onImport(c),
        }),
      ])
    )
  );
}

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
  // 新しい版があるときだけ出す（無いときに押させても「最新です」と言うだけ）
  refs.downloadRow.hidden = !info.newer;
}

/**
 * タブを切り替える。
 *
 * **フッタの「保存」はデータの保存先のもの**（AI 側は自前の保存ボタンを持つ）。
 * 出しっぱなしにすると、AI を変えたあとにこちらを押して「保存したのに効かない」
 * になるので、「一般」のときだけ出す。
 */
function showTab(name) {
  for (const button of refs.tabs.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === name));
  }
  for (const panel of dialog.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  refs.save.hidden = name !== "general";
}

/**
 * 公開の設定を描く。
 *
 * **下見をそのまま出す**（どこへ書かれるか・上書きになるか・カードが出ない理由）。
 * 「入れ替わります」の一言だけで押させない、という取り込みと同じ扱い。
 */
function paintPublish(info) {
  refs.pubDir.value = info.root || "";
  refs.pubBase.value = info.base_url || "";
  refs.pubLocked.hidden = !info.env_locked;
  if (info.env_locked) {
    refs.pubLocked.textContent =
      "環境変数 GLOSSPOP_PUBLISH_DIR / GLOSSPOP_PUBLISH_BASE_URL が設定されているので、" +
      "ここでの設定より優先されます。";
  }
  for (const node of [refs.pubDir, refs.pubBase, refs.pubSave]) node.disabled = info.env_locked;

  const plan = info.plan;
  if (!plan) {
    refs.pubPlan.textContent =
      "書き出し先のフォルダが決まっていないので、まだ公開できません。";
    return;
  }
  const changed = plan.files.filter((f) => f.overwrite).map((f) => f.name);
  const lines = [`${plan.dir} に ${plan.files.map((f) => f.name).join(" と ")} を書きます。`];
  if (changed.length) lines.push(`${changed.join(" と ")} は上書きになります。`);
  if (plan.url) lines.push(`公開後の URL: ${plan.url}`);
  lines.push(...(plan.warnings || []));
  refs.pubPlan.textContent = lines.join(" ");
}

/** 環境変数で固定されている項目は触らせない（書いても効かないため）。 */
function lockIfEnv(node, source, name) {
  const locked = source === "env";
  node.disabled = locked;
  return locked ? `${name}は環境変数で固定されています。` : "";
}

/**
 * AI の設定を描く。
 *
 * モデルの一覧は**サーバ経由で API から引く**（焼き込むと必ず古くなる）。
 * 取れなくても手入力できるよう、select ではなく datalist 付きの input にしてある。
 */
function paintAI(info) {
  refs.aiProvider.replaceChildren(
    ...info.providers.map((p) =>
      el("option", {
        value: p.id,
        text: p.available ? p.label : `${p.label}（使えません）`,
      })
    )
  );
  refs.aiProvider.value = info.provider;
  refs.aiEffort.replaceChildren(
    ...info.efforts.map((e) => el("option", { value: e.id, text: e.label }))
  );
  refs.aiEffort.value = info.effort;
  refs.aiModel.value = info.model;

  const spec = info.providers.find((p) => p.id === info.provider) || {};
  refs.aiHint.textContent = [spec.hint, info.reason].filter(Boolean).join(" ");
  refs.aiKeyRow.hidden = !spec.needs_key;
  refs.aiKey.placeholder =
    info.gemini_key_source === "env"
      ? "環境変数で設定済み（ここでの入力より優先されます）"
      : info.gemini_key_set
        ? "登録済み（変えるときだけ入力）"
        : "Gemini API キー（AI Studio で発行）";
  refs.aiKey.value = "";
  refs.aiKeyClear.disabled = !info.gemini_key_set || info.gemini_key_source === "env";

  // 文体と顔は共用の部品が描く。環境変数で固定されているときの一言だけ受け取る
  const notes = [
    lockIfEnv(refs.aiProvider, info.provider_source, "使う AI"),
    lockIfEnv(refs.aiModel, info.model_source, "モデル"),
    lockIfEnv(refs.aiEffort, info.effort_source, "思考の深さ"),
    styleEditor?.paint(info) || "",
  ].filter(Boolean);
  refs.aiLocked.hidden = !notes.length;
  refs.aiLocked.textContent = notes.join(" ");

  // 選んだ AI が使えないことは、タブを開かなくても分かるようにする
  refs.aiMark.hidden = info.available;
  refs.aiMark.title = info.reason;
}

/** 選べるモデルを datalist に入れる。取れなければ理由を出して手入力に任せる。 */
async function loadAIModels(provider) {
  refs.aiModelNote.textContent = "";
  try {
    const res = await api(`/api/ai/models?provider=${encodeURIComponent(provider)}`);
    refs.aiModelList.replaceChildren(
      ...(res.models || []).map((m) =>
        el("option", { value: m.id, text: m.label || m.id })
      )
    );
    if (provider === "gemini") {
      refs.aiModelNote.textContent =
        `${res.models.length} 件を Gemini API から取得しました（一覧に無い名前も入力できます）。`;
    }
  } catch (err) {
    refs.aiModelList.replaceChildren();
    refs.aiModelNote.textContent = `モデル一覧を取れませんでした: ${err.message}`;
  }
}

export async function openSettingsDialog() {
  build();
  refs.result.hidden = true;
  showTab("general");                     // 開き直したら必ず先頭のタブから
  styleEditor?.resetScope();              // 効いているほうを毎回先に見せる
  // ローカルの設定なので待たずに出せる
  refs.theme.value = currentTheme();
  refs.fontSize.value = currentFontSize();
  refs.firstOnly.checked = firstOnly();
  setStatus(refs.status, "読み込み中", "busy");
  dialog.showModal();

  // **listener は await の前に付ける。** ダイアログは開いた瞬間から操作できるのに、
  // 読み込みを待ってから付けると、その間の操作が黙って無視される（テーマを選んでも
  // 何も起きなかった）。extract.js の close を取り逃がした件と同じ形
  let info = null;

  // テーマと文字の大きさは選んだ瞬間に効かせる（保存ボタンは保存先だけの話）。
  // ⚙ はビューアの上に重なって開くので、下の本文がその場で変わるのが見える
  const onTheme = () => applyTheme(refs.theme.value);
  const onFontSize = () => applyFontSize(refs.fontSize.value);

  const onMode = () => syncMode();

  /** 隣のフォルダからデータを引き継ぐ。元は消さない。 */
  const onImport = async (candidate) => {
    setStatus(refs.status, "引き継ぎ中", "busy");
    try {
      const res = await api("/api/import", { method: "POST", body: { path: candidate.path } });
      refs.result.hidden = false;
      refs.result.textContent =
        `${candidate.name} から ${res.copy.copied.length} 件を引き継ぎました` +
        `（元のデータは残っています）。GlossPop を一度終了して、開き直してください。`;
      setStatus(refs.status, "引き継ぎました（再起動が必要です）");
      refs.importBox.hidden = true;
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
  };

  /** 書き出す範囲。空なら辞書全体（カテゴリ名は空白を含みうるので値のまま使う）。 */
  const exportQuery = () => (refs.exportScope.value
    ? `?category=${encodeURIComponent(refs.exportScope.value)}`
    : "");

  /**
   * 何語入って、**行き先が外に出る関係が何本あるか**を先に出す。
   *
   * 一部だけ渡すと、渡した先で相手の居ない関係ができる（保存はできるが、
   * リンクにも図の辺にもならない）。押したあとでは気付けないので、選んだ時点で
   * 数を見せる。書き出し自体は何も壊さないので、確認までは取らない。
   */
  const onExportScope = async () => {
    refs.exportNote.textContent = "";
    try {
      const plan = await api(`/api/export/plan${exportQuery()}`);
      const dangling = plan.dangling_count
        ? `。渡した先で行き先の無くなる関係が ${plan.dangling_count} 本あります`
          + `（例: ${plan.dangling.slice(0, 3).join("、")}）`
        : "";
      // 絵は圧縮が効かないので大きさも出す（zip がそのぶん重くなる）
      const shots = plan.maps + plan.images;
      const maps = shots
        ? `画像（地図 ${plan.maps} / 用語 ${plan.images} 枚。`
          + `${Math.round(plan.maps_bytes / 1024 / 1024 * 10) / 10} MB）も入ります。`
        : "";
      refs.exportNote.textContent =
        `${plan.partial ? "このカテゴリ" : "辞書全体"}で ${plan.entries} 語${dangling}。${maps}`;
    } catch (err) {
      refs.exportNote.textContent = err.message;
    }
  };

  /** 辞書を zip で書き出す。ブラウザに保存させるだけなので確認は要らない。 */
  const onExport = () => {
    // `api()` は JSON を返す前提なので、ダウンロードは素の遷移でやる
    const frame = el("a", { href: `/api/export${exportQuery()}`, download: "" });
    document.body.append(frame);
    frame.click();
    frame.remove();
    setStatus(refs.status, "書き出しました（ブラウザの保存先を見てください）");
  };

  /**
   * zip で辞書を置き換える。**このアプリで唯一データが消える操作。**
   *
   * 控えはサーバ側が必ず取る（人の手順に任せない）。ここでは、何が消えるかを
   * 数で示してから確認を取り、控えの場所を必ず画面に出す。
   */
  /** zip を送る。下見と本番で同じ形なので 1 か所にまとめる。 */
  const postArchive = async (path, file, mode) => {
    const res = await fetch(`${path}?mode=${encodeURIComponent(mode)}`, {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    return data;
  };

  const onImportFile = async (ev) => {
    const file = ev.target.files?.[0];
    ev.target.value = "";               // 同じファイルをもう一度選べるように
    if (!file) return;
    const mode = refs.importMode.value;

    // **先に下見を取る。** 「入れ替わります」の一言だけで押させると、何語が
    // 消えるのか分からないまま実行することになる
    setStatus(refs.status, "中身を確かめています", "busy");
    let plan;
    try {
      plan = await postArchive("/api/import-glossary/plan", file, mode);
    } catch (err) {
      setStatus(refs.status, err.message, "error");
      return;
    }
    setStatus(refs.status, "");

    const bits = [`足す ${plan.added_count} 語`, `上書き ${plan.updated_count} 語`];
    if (mode === "replace") bits.push(`消える ${plan.removed_count} 語`);
    bits.push(`変わらない ${plan.unchanged} 語`);
    // **地図の絵は語とは別に数える。** 置き換えでも消えない側なので、同じ行に
    // 混ぜると「消える」がどこまで掛かるのか分からなくなる
    const mapBits = [];
    const added = plan.maps_added_count + plan.images_added_count;
    const updated = plan.maps_updated_count + plan.images_updated_count;
    if (added) mapBits.push(`足す ${added} 枚`);
    if (updated) mapBits.push(`上書き ${updated} 枚`);
    const ok = confirm(
      `「${file.name}」を${mode === "merge" ? "併合" : "置き換え"}します。\n\n` +
        bits.join(" / ") + "\n" +
        (mapBits.length ? `地図の絵: ${mapBits.join(" / ")}（zip に無い絵は残ります）\n` : "") +
        "\n" +
        (mode === "merge"
          ? "両方にある用語は zip の側で上書きされます（手元の内容は控えに残ります）。\n"
          : "zip に無い用語は消えます。\n") +
        "実行の前に控えを自動で取ります。よろしいですか？"
    );
    if (!ok) return;

    setStatus(refs.status, "取り込み中", "busy");
    try {
      const data = await postArchive("/api/import-glossary", file, mode);
      const lines = [
        mode === "merge"
          ? `${data.added_count} 語を足し、${data.updated_count} 語を上書きしました。`
          : `${data.entries} 語 / ${data.categories} カテゴリに置き換えました。`,
        `控えは「${data.backup}」です（ここから戻せます）。`,
      ];
      const gained = data.maps_added_count + data.maps_updated_count
        + data.images_added_count + data.images_updated_count;
      if (gained) lines.push(`画像を ${gained} 枚入れました。`);
      // 名前を全部は出さないので、切ったことは言う
      if (data.truncated) lines.push(`件数が多いので名前の一覧は先頭 ${data.added.length} 件までです。`);
      // 消せなかった作業用フォルダは黙らない
      if (data.leftover) lines.push(`片付けられなかったフォルダ: ${data.leftover}`);
      refs.result.hidden = false;
      refs.result.textContent = lines.join(" ");
      setStatus(refs.status, "取り込みました");
      // 保存先は変わらないので再起動は要らない。topbar の語数だけ合わせる
      await paintEntryCount(document.getElementById("count"));
      await paintBackups();               // いま取った控えを一覧に出す
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
  };

  /**
   * 取り込み前の控え。**中を見て 1 件だけ戻せるようにする。**
   *
   * 併合の衝突は「取り込む側が勝つ」なので、上書きされた語は控えにしか残らない。
   * zip を手で開かせるのでは、その約束が半分しか果たせていない。
   * **古いものを自動では消さない** —— 控えは「消える前の唯一の写し」なので、
   * 勝手に捨てると約束のほうが壊れる。合計の大きさを出して人に決めさせる。
   */
  const paintBackups = async () => {
    try {
      const data = await api("/api/backups");
      refs.backupCount.textContent = data.items.length
        ? `（${data.items.length} 件 / ${fileSize(data.total_bytes)}）`
        : "（まだありません）";
      refs.backupList.replaceChildren(
        ...(data.items.length
          ? data.items.map(backupRow)
          : [el("p", {
            class: "hint",
            text: "取り込みの前に自動で取ります。まだ 1 つもありません。",
          })])
      );
    } catch (err) {
      refs.backupList.replaceChildren(el("p", { class: "status error", text: err.message }));
    }
  };

  const fileSize = (bytes) => (bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`);

  const when = (iso) => {
    const at = new Date(iso);
    return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
  };

  const backupRow = (item) => {
    const inside = el("div", { class: "backup-inside", hidden: true });
    const open = el("button", {
      type: "button",
      title: item.name,
      text: `${when(item.created_at)} — ${item.entries} 語 / ${fileSize(item.size)}`,
      onclick: () => openBackup(item, open, inside),
    });
    const drop = el("button", {
      type: "button",
      class: "ghost",
      title: "この控えを捨てる",
      "aria-label": `${item.name} を捨てる`,
      text: "🗑",
      onclick: async () => {
        if (!confirm(`控え「${item.name}」を捨てます。元には戻せません。よろしいですか？`)) return;
        try {
          await api(`/api/backups/${encodeURIComponent(item.name)}`, { method: "DELETE" });
          await paintBackups();
        } catch (err) {
          setStatus(refs.status, err.message, "error");
        }
      },
    });
    return el("div", { class: "backup-row" }, [
      el("div", { class: "backup-head" }, [open, drop]),
      inside,
    ]);
  };

  /** 控えの中身を出す。もう一度押せば畳む（一覧が縦に伸びっぱなしにならない）。 */
  const openBackup = async (item, button, box) => {
    if (!box.hidden) {
      box.hidden = true;
      button.setAttribute("aria-expanded", "false");
      return;
    }
    box.hidden = false;
    button.setAttribute("aria-expanded", "true");
    box.replaceChildren(el("p", { class: "hint", text: "読み込み中…" }));
    try {
      const data = await api(`/api/backups/${encodeURIComponent(item.name)}`);
      const rows = data.entries.map((entry) => restoreRow(item, entry));
      // **切ったことは言う**（「これで全部」と読ませない）
      if (data.truncated) {
        rows.push(el("p", {
          class: "hint",
          text: `${data.count} 語のうち先頭 ${data.entries.length} 件だけ出しています。`,
        }));
      }
      box.replaceChildren(...(rows.length
        ? rows
        : [el("p", { class: "hint", text: "この控えには用語が入っていません。" })]));
    } catch (err) {
      box.replaceChildren(el("p", { class: "status error", text: err.message }));
    }
  };

  const restoreRow = (item, entry) => {
    const status = el("span", { class: "hint" });
    const button = el("button", {
      type: "button",
      // **上書きになるかどうかを押す前に出す**（控えは取らないので、代わりに先に見せる）
      text: entry.here ? "戻す（上書き）" : "戻す",
      onclick: async () => {
        if (entry.here && !confirm(`「${entry.ref}」を控えの内容で上書きします。よろしいですか？`)) {
          return;
        }
        button.disabled = true;
        try {
          const res = await api(`/api/backups/${encodeURIComponent(item.name)}/restore`, {
            method: "POST",
            body: { ref: entry.ref },
          });
          status.textContent = res.overwritten ? "上書きしました" : "戻しました";
          await paintEntryCount(document.getElementById("count"));
        } catch (err) {
          status.textContent = err.message;
          button.disabled = false;
        }
      },
    });
    return el("div", { class: "backup-entry" }, [
      el("span", { class: "backup-ref", title: entry.ref, text: entry.ref }),
      status,
      button,
    ]);
  };

  /** 新しい版を隣に展開する。**自分自身は置き換えない。** */
  const onDownload = async () => {
    refs.download.disabled = true;
    setStatus(refs.status, "ダウンロード中（数十秒かかります）", "busy");
    try {
      const res = await api("/api/update/download", { method: "POST" });
      refs.result.hidden = false;
      refs.result.textContent =
        `${res.version} を「${res.dir}」に展開しました（${res.files} ファイル` +
        `${res.verified ? "、ハッシュを確認済み" : ""}）。` +
        "そちらの glosspop.exe を起動してください。いまのフォルダはそのまま残っています。";
      setStatus(refs.status, "展開しました");
    } catch (err) {
      setStatus(refs.status, err.message, "error");
    }
    refs.download.disabled = false;
  };

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

  /**
   * AI の選択を保存する。**保存先の設定とは別のボタンにしてある。**
   *
   * 効くのが「次の起動から」ではなく「次の呼び出しから」で意味が違ううえ、
   * 保存先が環境変数で固定されていると保存ボタンごと無効になるため、
   * 同じボタンに載せると AI だけ変えたい人が詰む。
   */
  const onAISave = async (extra = {}) => {
    refs.aiSave.disabled = true;
    setStatus(refs.aiStatus, "保存中", "busy");
    try {
      const body = {
        provider: refs.aiProvider.value,
        model: refs.aiModel.value.trim(),
        effort: refs.aiEffort.value,
        ...extra,
      };
      // 入力欄が空のときは鍵を触らない（毎回消しに行かないため）
      if (!("gemini_api_key" in body) && refs.aiKey.value.trim()) {
        body.gemini_api_key = refs.aiKey.value.trim();
      }
      const res = await api("/api/ai/settings", { method: "PUT", body });
      // 「保存できたか」と「いま使えるか」は別の話。混ぜると、CLI が無いだけで
      // 保存が失敗したように見える（使えない理由は paintAI が説明の行に出す）
      paintAI(res);
      setStatus(refs.aiStatus, "保存しました");
    } catch (err) {
      setStatus(refs.aiStatus, err.message, "error");
    }
    refs.aiSave.disabled = false;
  };

  const onAIProvider = async () => {
    // 選び直した時点で、その AI で選べるモデルに入れ替える
    refs.aiModel.value = "";
    await loadAIModels(refs.aiProvider.value);
    await onAISave();
  };

  const onTab = (ev) => {
    const button = ev.target.closest("[data-tab]");
    if (button) showTab(button.dataset.tab);
  };

  /** 矢印キーでもタブを移れるようにする（tablist の作法）。 */
  const onTabKey = (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    const buttons = [...refs.tabs.querySelectorAll("[data-tab]")];
    const at = buttons.findIndex((b) => b.getAttribute("aria-selected") === "true");
    const step = ev.key === "ArrowRight" ? 1 : buttons.length - 1;
    const next = buttons[(at + step) % buttons.length];
    showTab(next.dataset.tab);
    next.focus();
    ev.preventDefault();
  };

  // 本文の見せ方は押した瞬間に効かせる（保存ボタンは保存先だけの話）。
  // ビューアを開いたまま設定を開けるので、`base.js` 側が見ている画面に伝える
  const onFirstOnly = () => setFirstOnly(refs.firstOnly.checked);

  /** 公開の設定を保存する。**その場で効く**ので、保存したら下見を描き直す。 */
  const onPubSave = async () => {
    setStatus(refs.pubStatus, "保存中", "busy");
    refs.pubSave.disabled = true;
    try {
      const res = await api("/api/publish/settings", {
        method: "PUT",
        body: { dir: refs.pubDir.value.trim(), base_url: refs.pubBase.value.trim() },
      });
      paintPublish(res);
      setStatus(refs.pubStatus, "保存しました");
    } catch (err) {
      setStatus(refs.pubStatus, err.message, "error");
    }
    refs.pubSave.disabled = false;
  };

  const onAISaveClick = () => onAISave();
  const onAIKeyClear = () => onAISave({ gemini_api_key: "" });
  const onAIModelFetch = () => loadAIModels(refs.aiProvider.value);

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
      // 旧フォルダをどうすればいいか言わないと、消していいのか分からないまま残る
      if (res.copied_from) {
        lines.push(
          `元のデータは「${res.copied_from}」に残っています。` +
            "新しい場所で問題が無いことを確かめてから、手で片付けてください。"
        );
      }
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
  const onImportPick = () => refs.importFile.click();
  refs.modeApp.addEventListener("change", onMode);
  refs.modeCustom.addEventListener("change", onMode);
  refs.theme.addEventListener("change", onTheme);
  refs.fontSize.addEventListener("change", onFontSize);
  refs.export.addEventListener("click", onExport);
  refs.exportScope.addEventListener("change", onExportScope);
  refs.importPick.addEventListener("click", onImportPick);
  refs.importFile.addEventListener("change", onImportFile);
  refs.download.addEventListener("click", onDownload);
  refs.updateCheck.addEventListener("change", onUpdateToggle);
  refs.path.addEventListener("input", onMode);
  refs.pick.addEventListener("click", onPick);
  refs.save.addEventListener("click", onSave);
  refs.tabs.addEventListener("click", onTab);
  refs.tabs.addEventListener("keydown", onTabKey);
  refs.aiSave.addEventListener("click", onAISaveClick);
  refs.pubSave.addEventListener("click", onPubSave);
  refs.aiProvider.addEventListener("change", onAIProvider);
  refs.aiKeyClear.addEventListener("click", onAIKeyClear);
  refs.aiModelFetch.addEventListener("click", onAIModelFetch);
  refs.firstOnly.addEventListener("change", onFirstOnly);
  refs.cancel.addEventListener("click", finish);
  refs.close.addEventListener("click", finish);
  refs.form.addEventListener("submit", onSubmit);

  // **開いた時点で持っているものを描き、届いたら足す。** 選択肢には「辞書全体」が
  // 最初から入っているので、読み込みが遅くても書き出し自体はできる
  onExportScope();
  paintBackups();
  api("/api/categories")
    .then((tree) => {
      refs.exportScope.append(
        ...tree
          // 書き出すのは全体の辞書だけ（フォルダの辞書はフォルダごと運ぶ）
          .filter((node) => node.scope !== "local" && node.count > 0)
          .map((node) => el("option", { value: node.category, text: node.category }))
      );
    })
    .catch(() => {
      /* 選べなくても辞書全体は書き出せる。ここで止めない */
    });

  // AI の設定は保存先とは別に読む。片方が落ちてももう片方は使えるように
  // （**listener を付けたあとの最初の await より前に**、読み込みを始めない）
  api("/api/ai/settings")
    .then((res) => {
      paintAI(res);
      return loadAIModels(res.provider);
    })
    .catch((err) => setStatus(refs.aiStatus, err.message, "error"));

  // 公開の設定も別に読む（同じ理由）。**下見まで一緒に返ってくる**
  api("/api/publish")
    .then(paintPublish)
    .catch((err) => setStatus(refs.pubStatus, err.message, "error"));

  try {
    info = await api("/api/settings");
    paintPaths(info);
    paintMode(info);
    paintImports(info, onImport);
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
        refs.fontSize.removeEventListener("change", onFontSize);
        refs.export.removeEventListener("click", onExport);
        refs.exportScope.removeEventListener("change", onExportScope);
        refs.importPick.removeEventListener("click", onImportPick);
        refs.importFile.removeEventListener("change", onImportFile);
        refs.download.removeEventListener("click", onDownload);
        refs.updateCheck.removeEventListener("change", onUpdateToggle);
        refs.pubSave.removeEventListener("click", onPubSave);
        refs.path.removeEventListener("input", onMode);
        refs.pick.removeEventListener("click", onPick);
        refs.save.removeEventListener("click", onSave);
        refs.tabs.removeEventListener("click", onTab);
        refs.tabs.removeEventListener("keydown", onTabKey);
        refs.aiSave.removeEventListener("click", onAISaveClick);
        refs.aiProvider.removeEventListener("change", onAIProvider);
        refs.aiKeyClear.removeEventListener("click", onAIKeyClear);
        refs.aiModelFetch.removeEventListener("click", onAIModelFetch);
        refs.firstOnly.removeEventListener("change", onFirstOnly);
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
    title: "設定（データの保存先・AI・更新の確認）",
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
