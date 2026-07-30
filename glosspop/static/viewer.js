// ビューア: ソース読み込み → レンダリング → テキスト選択で辞書登録。
import { api, el, esc, externalLink, paintEntryCount, setStatus } from "./base.js";
import { openExtractDialog } from "./extract.js";
import { installGlossPopup } from "./popup.js";
import { installSelectionAdd } from "./select-add.js";

const $ = (id) => document.getElementById(id);
const doc = $("doc");
const docHead = $("docHead");
const docMeta = $("docMeta");
const termsList = $("terms");
const filesList = $("files");

/** 現在表示中のソース。保存後の再レンダリングに使う。 */
let source = null; // { text, kind, filename, contentPath }

installGlossPopup();

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
    paintTerms(res.terms);
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

function setSource(next) {
  source = next;
  selection.hide();
  note("");
  // 描画の完了を待ちたい呼び出し元 (初出へジャンプ) があるので promise を返す
  return renderCurrent();
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
  await setSource({ text: await file.text(), kind: "auto", filename: file.name });
  markCurrentFile(null);
}

$("file").addEventListener("change", async (ev) => {
  const file = ev.target.files?.[0];
  if (!file) return;
  await openLocalFile(file);
  ev.target.value = "";
});

$("showPaste").addEventListener("click", () => {
  const text = $("paste").value;
  if (!text.trim()) {
    $("paste").focus();
    return;
  }
  setSource({ text, kind: $("kind").value, filename: null });
  markCurrentFile(null);
});

$("firstOnly").addEventListener("change", renderCurrent);

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

// URL を開く (取得はサーバ側。CORS を踏まないため)
async function openUrl(url) {
  $("urlGo").disabled = true;
  setStatus($("urlStatus"), "読み込み中", "busy");
  try {
    const res = await api("/api/fetch", { method: "POST", body: { url } });
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

async function loadFileList() {
  filesList.replaceChildren(el("li", { class: "empty", text: "読み込み中…" }));
  try {
    paintFileList(await api("/api/content"));
  } catch (err) {
    filesList.replaceChildren(el("li", { class: "empty", text: `一覧を取得できません: ${err.message}` }));
  }
}

function paintFileList(res) {
  const root = $("rootStatus");
  root.className = "hint"; // 直前のエラー表示を戻す
  root.textContent = res.root + (res.is_default ? "（既定）" : "");
  root.title = res.root;
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

async function openContent(path) {
  try {
    const res = await api(`/api/content/${path.split("/").map(encodeURIComponent).join("/")}`);
    // epub は HTML、pdf はテキストとして返ってくる。拡張子からは判断できない
    await setSource({
      text: res.text,
      kind: res.kind || "auto",
      filename: res.name,
      contentPath: path,
      title: res.title || "",
    });
    markCurrentFile(path);
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
  if (!path) return;
  await openContent(path);
  const term = params.get("term");
  if (!term) return;
  // annotate 済みの本文から、その語の最初のリンクを探して寄せる
  const hit = doc.querySelector(`[data-gloss="${CSS.escape(term)}"]`);
  if (hit) {
    hit.scrollIntoView({ block: "center" });
    hit.classList.add("gloss-flash");
  } else {
    note(`「${term}」はこの文書に見つかりませんでした`, "error");
  }
}

paintEntryCount($("count"));
loadFileList().then(openFromQuery);
