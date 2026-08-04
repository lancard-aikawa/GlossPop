// ビューア: ソース読み込み → レンダリング → テキスト選択で辞書登録。
import { api, el, esc, externalLink, paintEntryCount, setStatus } from "./base.js";
import { openExtractDialog } from "./extract.js";
import { installGlossPopup } from "./popup.js";
import { createTracker, keyFor } from "./progress.js";
import { installOverlay, open } from "./overlay.js";
import { openRelationsDialog } from "./relations-draft.js";
import { installSelectionAdd } from "./select-add.js";
import { available as speechAvailable, createReader } from "./speech.js";

const $ = (id) => document.getElementById(id);
const doc = $("doc");
const docHead = $("docHead");
const docMeta = $("docMeta");
const termsList = $("terms");
const filesList = $("files");

/** 現在表示中のソース。保存後の再レンダリングに使う。 */
let source = null; // { text, kind, filename, contentPath }

installGlossPopup();

// 読書位置。スクロールするのは本文そのものではなく外側の main
// (`.layout > * { overflow: auto }`)
const tracker = createTracker({ container: doc.parentElement, doc });

const selection = installSelectionAdd({
  root: doc,
  source: () => source?.url || source?.contentPath || source?.filename || "",
  onSaved: () => Promise.all([renderCurrent(), paintEntryCount($("count"))]),
});

// --------------------------------------------------------------------- 描画

async function renderCurrent() {
  if (!source) return;
  doc.setAttribute("aria-busy", "true");
  try {
    const res = await api("/api/render", {
      method: "POST",
      body: {
        text: source.text,
        kind: source.kind || "auto",
        filename: source.filename || null,
        base_url: source.url || "",
        title: source.title || "",
        first_only: $("firstOnly").checked,
      },
    });
    doc.innerHTML = res.html || '<p class="empty">(空のドキュメント)</p>';
    docHead.hidden = false;
    paintDocMeta(res);
    paintDocGraphLink();
    paintTerms(res.terms);
    // 段落の番号で覚えているので、描き直したら対応づけ直す
    paintToc(source.sections || []);
    document.title = `${res.title || sourceLabel() || "テキスト"} — GlossPop`;
  } catch (err) {
    doc.innerHTML = `<p class="status error">表示できません: ${esc(err.message)}</p>`;
  } finally {
    doc.removeAttribute("aria-busy");
  }
}

function sourceLabel() {
  if (!source) return "";
  return source.url || source.contentPath || source.filename || "貼り付けたテキスト";
}

function paintDocMeta(res) {
  const chars = source.text.length.toLocaleString("ja-JP");
  const tail = ` — ${chars} 文字 / ${res.terms.length} 語ヒット`;
  docMeta.replaceChildren();
  if (source.url) {
    docMeta.append(externalLink(source.url), document.createTextNode(tail));
  } else {
    docMeta.textContent = sourceLabel() + tail;
  }
}

/**
 * 「この文書の相関図」へのリンク。
 *
 * サーバは `?doc=` を content の中のパスとして読み直すので、**フォルダの
 * ファイルを開いているときだけ**出せる（貼り付け・ドロップ・URL には
 * 読み直せる道が無い）。出しておいて絞れないより、出さないほうがまし。
 */
function paintDocGraphLink() {
  const link = $("docGraph");
  const path = source?.contentPath || "";
  link.href = path ? `/graph?doc=${encodeURIComponent(path)}` : "/graph";
  link.hidden = !path;
}

function paintTerms(terms) {
  if (!terms.length) {
    termsList.replaceChildren(el("li", { class: "empty", text: "まだヒットなし" }));
    return;
  }
  termsList.replaceChildren(
    ...terms.map((t) =>
      el("li", {}, [
        el("a", { href: t.url }, [
          el("span", { text: t.term }),
          el("span", { class: "cat", text: t.path_label }),
        ]),
      ])
    )
  );
}

//: 最後に開いていた「フォルダの中のファイル」。相関図や辞書へ寄り道して戻った
//: ときに続きから読めるようにする。**覚えるのはフォルダの中のファイルだけ** ——
//: 貼り付け・ドロップ・URL は読み直す道が無いので、覚えると嘘になる
//: （`progress.js` が段落の位置を覚えるのと対で、こちらは「どれを」を覚える）
const LAST_KEY = "glosspop.viewer.last";

function rememberOpened(next) {
  try {
    if (next?.contentPath && currentRoot) {
      localStorage.setItem(
        LAST_KEY, JSON.stringify({ root: currentRoot, path: next.contentPath })
      );
    } else {
      // 貼り付けや URL に切り替えたら忘れる。残すと、次に開いたときに
      // 「読んでいたはずのもの」と違うものが出る
      localStorage.removeItem(LAST_KEY);
    }
  } catch { /* localStorage が使えない環境では覚えないだけ */ }
}

function lastOpened() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAST_KEY) || "null");
    return saved && typeof saved.path === "string" ? saved : null;
  } catch {
    return null;
  }
}

/**
 * 表示する文書を差し替える。
 *
 * ``restore`` を偽にすると読書位置を戻さない（初出へ飛ぶときのように、
 * 別の場所へ寄せることが決まっている場合）。
 */
async function setSource(next, { restore = true, highlight = "" } = {}) {
  // **本文を描き替える前に**位置を書き出す。あとだと新しい本文の位置を
  // 前の文書の鍵で保存してしまう
  tracker.switchTo(keyFor(next, currentRoot));
  source = next;
  rememberOpened(next);
  selection.hide();
  reader?.reset();      // 別の文書になったので読み上げは打ち切る
  note("");
  await renderCurrent();
  if (highlight) return highlightText(highlight);
  if (!restore) return;
  const at = tracker.restore();
  // 戻さないなら先頭から。前の文書のスクロール位置が残ると途中から始まって見える
  if (at) noteResumed(at);
  else tracker.toTop();
}

// --------------------------------------------------------------------- 目次
//
// **位置を作り直さない。** epub は章ごとに見出しが、pdf はページごとに `【p.N】` が
// すでに本文へ入っている（`documents.py`）ので、描き終わった本文を頭から 1 回なぞって
// 段落の番号に対応づける。アンカーを埋める必要が無いぶん、サニタイザや
// Markdown の描画に手を入れずに済む。

/**
 * 節の名前を段落の番号に対応づける。
 *
 * **頭から順に、前の節より後ろだけを探す。** 章の名前が本文にも出てくることは
 * あるので、文書全体から最初の一致を採ると手前へ飛ぶ。節は必ず文書順に並ぶ、
 * という性質でそれを避けている。
 */
function locateSections(labels) {
  const blocks = [...doc.children];
  const found = [];
  let cursor = 0;
  for (const label of labels) {
    const needle = label.toLowerCase();
    const at = blocks.findIndex(
      (block, i) => i >= cursor && (block.textContent || "").toLowerCase().includes(needle)
    );
    if (at < 0) continue;              // 見つからない節は出さない（数だけ知らせる）
    found.push({ label, block: at });
    cursor = at + 1;
  }
  return found;
}

function paintToc(labels) {
  const list = $("toc");
  const head = $("tocHead");
  const note = $("tocNote");
  const found = labels.length ? locateSections(labels) : [];
  head.hidden = list.hidden = !found.length;
  list.replaceChildren(
    ...found.map((s) =>
      el("li", {}, [
        el("button", {
          type: "button",
          title: s.label,
          text: s.label,
          onclick: () => {
            const block = doc.children[s.block];
            if (!block) return;
            block.scrollIntoView({ block: "start" });
            block.classList.add("gloss-flash");
          },
        }),
      ])
    )
  );
  // 黙って欠けた目次を出さない
  const missing = labels.length - found.length;
  note.hidden = !missing;
  note.textContent = missing ? `${missing} 件は本文の中に見つかりませんでした` : "";
}

/**
 * 語を含む最初の段落へ寄せて光らせる（横断検索から開いたとき）。
 *
 * 初出ジャンプ (`?term=`) は登録済みの語なので ``[data-gloss]`` を探せるが、
 * 検索語は辞書に無いことのほうが多い。見えているテキストで探す。
 */
function highlightText(text) {
  const needle = text.toLowerCase();
  for (const block of doc.children) {
    if (!(block.textContent || "").toLowerCase().includes(needle)) continue;
    block.scrollIntoView({ block: "center" });
    block.classList.add("gloss-flash");
    return;
  }
  note(`「${text}」はこの文書に見つかりませんでした`, "error");
}

/** 「前回の続きから出しました」と、先頭へ戻す手段を出す。 */
function noteResumed({ block, total }) {
  note(`前回の続き（${block + 1} / ${total} 段落）から表示しています。`);
  $("docStatus").append(
    " ",
    el("button", {
      type: "button",
      class: "ghost",
      text: "先頭から読む",
      onclick: () => {
        tracker.reset();
        note("");
      },
    })
  );
}

/** 文書の上に出す一言 (リンクを追えなかった理由など)。 */
function note(message, kind = "") {
  const node = $("docStatus");
  setStatus(node, message, kind);
  node.hidden = !message;
}

// -------------------------------------------------- 文書内リンクを追いかける

/** ビューアで開ける拡張子。サーバ側の CONTENT_SUFFIXES と揃える。 */
const OPENABLE = /\.(md|markdown|mdown|txt|html?)$/i;

/** content 内の相対リンクを content ルートからのパスに直す。 */
function resolveContentPath(fromPath, href) {
  const dir = fromPath.includes("/") ? fromPath.replace(/[^/]*$/, "") : "";
  // URL に解決させると ../ や ./ の処理を自前で書かずに済む
  const target = new URL(href, `http://doc.invalid/${dir}`);
  return decodeURIComponent(target.pathname).replace(/^\//, "");
}

doc.addEventListener("click", (ev) => {
  if (ev.defaultPrevented || ev.button !== 0) return;
  if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return; // 別タブで開く操作は邪魔しない

  const a = ev.target.closest("a[href]");
  if (!a || !doc.contains(a)) return;
  const href = a.getAttribute("href") || "";
  // ページ内アンカーと辞書ページ (/glossary/...) はそのままブラウザに任せる
  if (!href || href.startsWith("#") || href.startsWith("/")) return;
  if (a.classList.contains("gloss-link")) return;

  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
    if (!/^https?:/i.test(href)) return; // mailto: などは触らない
    ev.preventDefault();
    // URL で読んでいる文書なら続きもビューアで読む。ローカル文書からは別タブへ
    if (source?.url) openUrl(href);
    else window.open(href, "_blank", "noopener");
    return;
  }

  ev.preventDefault(); // 相対リンク: サーバの 404 に飛ばさない
  if (source?.url) return openUrl(new URL(href, source.url).href);

  if (source?.contentPath) {
    const path = resolveContentPath(source.contentPath, href);
    if (OPENABLE.test(path)) return openContent(path);
    return note(`ビューアで開ける形式ではありません: ${path}`, "error");
  }

  note(
    "開いたファイル単体では隣のファイルを読めません（ブラウザの制限）。" +
      "リンクを辿るには、そのファイルがあるフォルダを左で開いてください。",
    "error"
  );
});

// ------------------------------------------------------------- ソース選択

$("pick").addEventListener("click", () => $("file").click());

/** epub / pdf はブラウザ側では中身を取り出せない（サーバが読む必要がある）。 */
const SERVER_ONLY = /\.(epub|pdf)$/i;

async function openLocalFile(file) {
  if (SERVER_ONLY.test(file.name)) {
    docHead.hidden = false;
    note(
      `${file.name} はサーバ側で読む必要があります。` +
        "そのファイルがあるフォルダを左で開いてください。",
      "error"
    );
    return;
  }
  await paintUrlDictionary("");   // URL ではないのでフォルダ側の辞書に戻す
  await setSource({ text: await file.text(), kind: "auto", filename: file.name });
  markCurrentFile(null);
}

$("file").addEventListener("change", async (ev) => {
  const file = ev.target.files?.[0];
  if (!file) return;
  await openLocalFile(file);
  ev.target.value = "";
});

$("showPaste").addEventListener("click", async () => {
  const text = $("paste").value;
  if (!text.trim()) {
    $("paste").focus();
    return;
  }
  await paintUrlDictionary("");
  setSource({ text, kind: $("kind").value, filename: null });
  markCurrentFile(null);
});

$("firstOnly").addEventListener("change", renderCurrent);

// ------------------------------------------------------------- 読み上げ

/** Web Speech API が無いブラウザでは、ボタンごと出さない。 */
const reader = speechAvailable()
  ? createReader({ root: doc, onState: paintSpeech })
  : null;

function paintSpeech(state) {
  const bar = $("speechBar");
  bar.hidden = !state.playing;
  $("speak").hidden = state.playing;
  $("speakToggle").textContent = state.paused ? "▶" : "⏸";
  $("speakToggle").title = state.paused ? "再開" : "一時停止";
  $("speakWhere").textContent = state.total
    ? `${Math.min(state.index + 1, state.total)} / ${state.total} 段落`
    : "";
  const voice = $("speakVoice");
  // 音声の一覧は一度だけ作る (毎回作ると選択中のものが飛ぶ)
  if (voice.options.length !== state.voices.length) {
    voice.replaceChildren(
      ...state.voices.map((v) => el("option", { value: v.name, text: `${v.name} (${v.lang})` }))
    );
  }
  if (state.voiceName) voice.value = state.voiceName;
  $("speakRate").value = String(state.rate);
}

if (reader) {
  reader.prepare().then((voices) => {
    // 音声が 1 つも無い環境では読み上げられないので出さない
    if (!voices.length) $("speak").dataset.unsupported = "1";
  });
  $("speak").addEventListener("click", () => {
    if ($("speak").dataset.unsupported) {
      note("この環境には読み上げに使える音声がありません。", "error");
      return;
    }
    if (!reader.start()) note("読み上げる本文がありません。", "error");
  });
  $("speakToggle").addEventListener("click", () => reader.toggle());
  $("speakStop").addEventListener("click", () => reader.stop());
  $("speakPrev").addEventListener("click", () => reader.step(-1));
  $("speakNext").addEventListener("click", () => reader.step(1));
  $("speakVoice").addEventListener("change", (ev) => reader.setVoice(ev.target.value));
  $("speakRate").addEventListener("change", (ev) => reader.setRate(Number(ev.target.value)));
  // ページを離れても喋り続けるのを防ぐ (speechSynthesis はページより長生きする)
  window.addEventListener("pagehide", () => reader.stop());
}

// 候補を挙げて、選んだ語をまとめて登録する (表示中の文書 / フォルダ全体)
async function runExtract(button, options) {
  button.disabled = true;
  try {
    const saved = await openExtractDialog(options);
    if (saved) await Promise.all([renderCurrent(), paintEntryCount($("count"))]);
  } finally {
    button.disabled = false;
  }
}

$("extract").addEventListener("click", () => {
  if (!source) return;
  runExtract($("extract"), { text: source.text, source: sourceLabel() });
});

$("extractFolder").addEventListener("click", () => {
  runExtract($("extractFolder"), { folder: true });
});

// 登録済みの用語どうしの関係を、**表示中の文書から**探す。
//
// 相関図には置かない —— あちらは辞書全体を出すので、下書きが読む範囲
// （開いているもの）と一致しない（→ docs/open-questions.md の 7 番）。
// 以前は抽出ダイアログの中にしか無く、読むたびに抽出と登録を通し直していた。
$("draftRelations").addEventListener("click", async () => {
  if (!source) return;
  const button = $("draftRelations");
  button.disabled = true;
  try {
    if (await openRelationsDialog({ text: source.text, source: sourceLabel() })) {
      await Promise.all([renderCurrent(), paintEntryCount($("count"))]);
    }
  } finally {
    button.disabled = false;
  }
});

// URL を開く (取得はサーバ側。CORS を踏まないため)
async function openUrl(url) {
  $("urlGo").disabled = true;
  setStatus($("urlStatus"), "読み込み中", "busy");
  try {
    const res = await api("/api/fetch", { method: "POST", body: { url } });
    // 辞書の文脈を URL 側へ切り替えてから描画する (フォルダ側の辞書は効かなくなる)
    await paintUrlDictionary(res.url);
    setSource({
      text: res.text,
      kind: res.kind,
      filename: null,
      url: res.url,
      title: res.title,
    });
    markCurrentFile(null);
    $("url").value = res.url;
    setStatus($("urlStatus"), res.title || res.url);
  } catch (err) {
    setStatus($("urlStatus"), err.message, "error");
  } finally {
    $("urlGo").disabled = false;
  }
}

$("urlForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const url = $("url").value.trim();
  if (!url) return $("url").focus();
  openUrl(url);
});

// ------------------------------------------------- URL ごとのローカル辞書

/** いま読んでいる URL をサーバに伝え、効いている辞書を表示する。 */
async function paintUrlDictionary(url) {
  const line = $("urlDict");
  const form = $("urlDictForm");
  try {
    const info = await api("/api/url-context", { method: "POST", body: { url: url || "" } });
    if (!url) {
      line.hidden = form.hidden = true;
      return;
    }
    line.hidden = false;
    form.hidden = Boolean(info.prefix);
    line.textContent = info.prefix
      ? `この URL の辞書: ${info.prefix}`
      : "この URL の辞書はまだありません（範囲を決めて作れます）";
    $("urlPrefix").value = info.suggested_prefix || "";
  } catch {
    line.hidden = form.hidden = true;
  }
}

$("urlDictForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const prefix = $("urlPrefix").value.trim();
  if (!prefix) return $("urlPrefix").focus();
  $("urlDictGo").disabled = true;
  try {
    await api("/api/url-dictionary", { method: "POST", body: { prefix } });
    await paintUrlDictionary(source?.url || "");
    await renderCurrent();   // 新しい辞書でリンクを引き直す
  } catch (err) {
    setStatus($("urlStatus"), err.message, "error");
  } finally {
    $("urlDictGo").disabled = false;
  }
});

async function loadFileList() {
  filesList.replaceChildren(el("li", { class: "empty", text: "読み込み中…" }));
  try {
    const res = await api("/api/content");
    paintFileList(res);
    return res;
  } catch (err) {
    filesList.replaceChildren(el("li", { class: "empty", text: `一覧を取得できません: ${err.message}` }));
    return null;
  }
}

function paintFileList(res) {
  const root = $("rootStatus");
  root.className = "hint"; // 直前のエラー表示を戻す
  root.textContent = res.root + (res.is_default ? "（既定）" : "");
  root.title = res.root;
  // 辞書が親フォルダにあるときは黙って使わない (1 巻 2 巻の共有で起きる)
  if (res.local_is_ancestor) {
    root.textContent += ` — このフォルダの辞書: ${res.local_dir}`;
    root.title = `${res.root}\n辞書: ${res.local_dir}`;
  }
  $("root").value = res.is_default ? "" : res.root;
  currentRoot = res.root;
  paintFolderMenu();

  if (!res.files.length) {
    filesList.replaceChildren(
      el("li", { class: "empty", text: "このフォルダに .md / .txt / .html を置くとここに出ます" })
    );
    return;
  }
  filesList.replaceChildren(
    ...res.files.map((f) =>
      el("li", {}, [
        el("button", {
          type: "button",
          title: f.path,
          "data-path": f.path,
          text: f.path,
          onclick: () => openContent(f.path),
        }),
      ])
    )
  );
  if (res.truncated) {
    filesList.append(
      el("li", { class: "empty", text: `${res.files.length} 件で打ち切りました（多すぎます）` })
    );
  }
}

// --------------------------------------------------------- 本文の横断検索

/**
 * 開いているフォルダの本文を横断して探す。
 *
 * 索引は持たない（サーバがその場で読む）。**打ち切りは必ず画面に出す** ——
 * 黙って切ると「この語は無かった」と区別が付かない。
 */
async function runContentSearch(query) {
  const box = $("searchResults");
  setStatus($("searchStatus"), "検索中", "busy");
  $("searchGo").disabled = true;
  try {
    const res = await api(`/api/content-search?q=${encodeURIComponent(query)}`);
    paintSearchResults(res);
  } catch (err) {
    box.hidden = true;
    setStatus($("searchStatus"), err.message, "error");
  } finally {
    $("searchGo").disabled = false;
  }
}

function paintSearchResults(res) {
  const box = $("searchResults");
  box.hidden = false;
  if (!res.results.length) {
    box.replaceChildren(el("p", { class: "empty", text: `「${res.query}」は見つかりませんでした` }));
  } else {
    box.replaceChildren(
      ...res.results.map((file) =>
        el("section", { class: "search-file" }, [
          el("h3", {}, [
            el("span", { text: file.title || file.name }),
            el("span", { class: "count", text: `${file.count} 件` }),
          ]),
          el("ul", { class: "filelist" }, file.hits.map((hit) =>
            el("li", {}, [
              el("button", {
                type: "button",
                title: `${file.path} ${hit.locator}`,
                onclick: () => openContent(file.path, { highlight: res.query }),
              }, [
                el("span", { class: "loc", text: hit.locator }),
                el("span", { text: hit.snippet }),
              ]),
            ])
          )),
          // 1 ファイルから出すのは先頭のいくつかだけ。残りがあることは言う
          file.count > file.hits.length
            ? el("p", { class: "hint", text: `ほか ${file.count - file.hits.length} 件` })
            : null,
        ])
      )
    );
  }
  const notes = [`${res.total_hits} 件 / ${res.files_scanned} 文書を読みました`];
  if (res.files_truncated) notes.push("文書が多いので途中で打ち切りました");
  if (res.hits_truncated) notes.push("ヒットが多いので途中で打ち切りました");
  if (res.skipped.length) notes.push(`読めなかったファイル ${res.skipped.length} 件`);
  setStatus($("searchStatus"), notes.join(" — "), res.files_truncated || res.hits_truncated ? "error" : "");
}

$("searchForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const query = $("contentQ").value.trim();
  if (!query) {
    // 空にして押したら結果を畳む（一覧に戻る手段が要る）
    $("searchResults").hidden = true;
    setStatus($("searchStatus"), "");
    return;
  }
  runContentSearch(query);
});

// ------------------------------------------------- フォルダの切り替えと履歴

const FOLDERS_KEY = "glosspop.folders";
const MAX_RECENT = 8;

/** {recent: [path], favorites: [path]} を localStorage に持つ。 */
function loadFolders() {
  try {
    const data = JSON.parse(localStorage.getItem(FOLDERS_KEY) || "{}");
    return { recent: data.recent || [], favorites: data.favorites || [] };
  } catch {
    return { recent: [], favorites: [] };
  }
}

function saveFolders(data) {
  try {
    localStorage.setItem(FOLDERS_KEY, JSON.stringify(data));
  } catch {
    /* プライベートモード等で書けなくても機能自体は動く */
  }
}

let currentRoot = "";

function rememberFolder(path) {
  if (!path) return;
  const data = loadFolders();
  data.recent = [path, ...data.recent.filter((p) => p !== path)].slice(0, MAX_RECENT);
  saveFolders(data);
}

function paintFolderMenu() {
  const { recent, favorites } = loadFolders();
  const select = $("recent");
  const groups = [];
  const option = (path) => el("option", { value: path, text: path, title: path });
  if (favorites.length) {
    groups.push(el("optgroup", { label: "お気に入り" }, favorites.map(option)));
  }
  const rest = recent.filter((p) => !favorites.includes(p));
  if (rest.length) {
    groups.push(el("optgroup", { label: "最近開いた" }, rest.map(option)));
  }
  select.replaceChildren(
    el("option", { value: "", text: groups.length ? "フォルダを選ぶ…" : "（履歴なし）" }),
    ...groups
  );
  select.value = "";
  select.disabled = !groups.length;
  $("favToggle").textContent = favorites.includes(currentRoot) ? "★" : "☆";
  $("favToggle").disabled = !currentRoot;
}

/** フォルダを開く。空文字なら既定に戻る。 */
async function openRoot(path) {
  $("rootGo").disabled = $("pickFolder").disabled = true;
  try {
    const res = await api("/api/content-root", { method: "POST", body: { path } });
    // 別のフォルダの結果を残さない（押すと開けないファイルが並ぶ）
    $("searchResults").hidden = true;
    setStatus($("searchStatus"), "");
    paintFileList(res);
    markCurrentFile(null);
    if (!res.is_default) rememberFolder(res.root);
    paintFolderMenu();
  } catch (err) {
    setStatus($("rootStatus"), err.message, "error");
  } finally {
    $("rootGo").disabled = $("pickFolder").disabled = false;
  }
}

$("rootForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  openRoot($("root").value.trim());
});

$("recent").addEventListener("change", () => {
  const path = $("recent").value;
  if (path) openRoot(path);
});

// OS のフォルダ選択ダイアログはサーバ側で開く (ブラウザは絶対パスをくれない)
$("pickFolder").addEventListener("click", async () => {
  $("pickFolder").disabled = true;
  setStatus($("rootStatus"), "フォルダ選択ダイアログを開いています", "busy");
  try {
    const res = await api("/api/pick-folder", {
      method: "POST",
      body: { initial: currentRoot },
    });
    if (res.cancelled) paintFileList(await api("/api/content"));
    else await openRoot(res.path);
  } catch (err) {
    setStatus($("rootStatus"), err.message, "error");
  } finally {
    $("pickFolder").disabled = false;
  }
});

$("favToggle").addEventListener("click", () => {
  if (!currentRoot) return;
  const data = loadFolders();
  data.favorites = data.favorites.includes(currentRoot)
    ? data.favorites.filter((p) => p !== currentRoot)
    : [currentRoot, ...data.favorites];
  saveFolders(data);
  paintFolderMenu();
});

function markCurrentFile(path) {
  for (const btn of filesList.querySelectorAll("button")) {
    if (path && btn.dataset.path === path) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
  }
}

async function openContent(path, { restore = true, highlight = "" } = {}) {
  try {
    await paintUrlDictionary("");   // URL を読むのをやめる = フォルダ側の辞書に戻す
    const res = await api(`/api/content/${path.split("/").map(encodeURIComponent).join("/")}`);
    // epub は HTML、pdf はテキストとして返ってくる。拡張子からは判断できない
    await setSource(
      {
        text: res.text,
        kind: res.kind || "auto",
        filename: res.name,
        contentPath: path,
        title: res.title || "",
        // 章 / ページの名前。目次はこれを本文の段落に対応づけて作る
        sections: res.sections || [],
      },
      { restore, highlight }
    );
    markCurrentFile(path);
    // 一部だけ欠けた本文は「全部読めている」と区別が付かないので必ず知らせる
    // (setSource が note を消すので、そのあとで出す)
    if (res.skipped?.length) {
      note(
        `読めなかった章が ${res.skipped.length} 件あります: ` +
          res.skipped.slice(0, 5).join("、") +
          (res.skipped.length > 5 ? " ほか" : ""),
        "error"
      );
    }
  } catch (err) {
    // リンクを辿って失敗したときは、いま読んでいる文書を壊さない
    if (source) note(`開けません: ${err.message}`, "error");
    else doc.innerHTML = `<p class="status error">${esc(err.message)}</p>`;
  }
}

$("reloadFiles").addEventListener("click", loadFileList);

// ドラッグ&ドロップ
let dragDepth = 0;
window.addEventListener("dragenter", (ev) => {
  if (!ev.dataTransfer?.types.includes("Files")) return;
  dragDepth++;
  document.body.classList.add("dropping");
});
window.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    document.body.classList.remove("dropping");
  }
});
window.addEventListener("dragover", (ev) => {
  if (ev.dataTransfer?.types.includes("Files")) ev.preventDefault();
});
window.addEventListener("drop", async (ev) => {
  dragDepth = 0;
  document.body.classList.remove("dropping");
  const file = ev.dataTransfer?.files?.[0];
  if (!file) return;
  ev.preventDefault();
  await openLocalFile(file);
});

// ------------------------------------------------------------------ 起動

/** 用語の初出へ飛ぶ。``/?open=<content パス>&term=<用語>`` で開かれる。 */
async function openFromQuery() {
  const params = new URLSearchParams(location.search);
  const path = params.get("open");
  if (!path) return false;
  const term = params.get("term");
  // 初出へ飛ぶと決まっているときは読書位置を戻さない（戻してもすぐ上書きされ、
  // 「前回の続き」の案内だけが嘘になる）
  await openContent(path, { restore: !term });
  if (term) flashTerm(term);
  return true;
}

/** annotate 済みの本文から、その語の最初のリンクを探して寄せる。 */
function flashTerm(term) {
  const hit = doc.querySelector(`[data-gloss="${CSS.escape(term)}"]`);
  if (hit) {
    hit.scrollIntoView({ block: "center" });
    hit.classList.add("gloss-flash");
  } else {
    note(`「${term}」はこの文書に見つかりませんでした`, "error");
  }
}

/**
 * 前に読んでいたファイルを開き直す。
 *
 * 相関図・辞書・用語ページへ寄り道して戻ると、**開いていた本文が消えて
 * 案内文に戻っていた**（読書位置だけ覚えていても、開き直すのは手作業）。
 *
 * 開くのは**いま一覧に出ているファイルだけ**。フォルダを切り替えた後や、
 * 外で消された後に `openContent` を呼ぶと、ページを開いた瞬間にエラーが出る。
 */
async function restoreLast(listing) {
  const saved = lastOpened();
  if (!saved || !listing || saved.root !== listing.root) return;
  if (!listing.files?.some((f) => f.path === saved.path)) return;
  await openContent(saved.path);
}

/**
 * ビューアの現在地を URL にしたもの（覆いを閉じたときに戻す先）。
 *
 * 開いているものをそのまま指すので、その状態で再読み込みしても同じものが出る。
 */
function viewerUrl() {
  return source?.contentPath ? `/?open=${encodeURIComponent(source.contentPath)}` : "/";
}

// 辞書・用語・相関図・点検は**ビューアの上に重ねる**。ページとして開き直すと、
// 戻るたびに本文を取り直して描き直すことになる（→ overlay.js の頭）
installOverlay({
  viewerUrl,
  onClose: ({ changed }) => {
    // **変わったときだけ描き直す。** 毎回描き直すと重ねた意味が無くなる
    if (!changed) return;
    paintEntryCount($("count"));
    return renderCurrent();
  },
  // 覆いの中からビューアを名指しで呼ぶリンク（用語ページの「初出: 〇〇.md L.42 →」）
  onViewerLink: (url) => {
    const path = url.searchParams.get("open");
    if (!path) return;
    const term = url.searchParams.get("term");
    // 初出へ飛ぶと決まっているときは読書位置を戻さない（すぐ上書きされる）
    openContent(path, { restore: !term }).then(() => {
      if (term) flashTerm(term);
    });
  },
});

paintEntryCount($("count"));
loadFileList().then(async (listing) => {
  if (await openFromQuery()) return;   // URL の指定が最優先
  if (await openOverlayFromLocation()) return;
  await restoreLast(listing);
});

/**
 * `/glossary` などを直接開かれたとき。
 *
 * サーバはそれぞれのページを返すので普段はここを通らないが、覆いを開いたまま
 * 再読み込みされた場合に**ビューアの URL で辞書のページが要求される**ことがある
 * （履歴に積んだ URL はビューアのものではないので、実際にはサーバ側のページが
 * 返る）。念のため、ビューアが `/` 以外で動いていたら覆いを開いておく。
 */
async function openOverlayFromLocation() {
  if (location.pathname === "/") return false;
  return open(location.href, { push: false });
}
