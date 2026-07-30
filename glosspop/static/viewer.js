// ビューア: ソース読み込み → レンダリング → テキスト選択で辞書登録。
import { api, el, esc, externalLink, paintEntryCount, setStatus } from "./base.js";
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
  renderCurrent();
}

// ------------------------------------------------------------- ソース選択

$("pick").addEventListener("click", () => $("file").click());

$("file").addEventListener("change", async (ev) => {
  const file = ev.target.files?.[0];
  if (!file) return;
  setSource({ text: await file.text(), kind: "auto", filename: file.name });
  markCurrentFile(null);
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

// URL を開く (取得はサーバ側。CORS を踏まないため)
$("urlForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const url = $("url").value.trim();
  if (!url) return $("url").focus();
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
});

async function loadFileList() {
  filesList.replaceChildren(el("li", { class: "empty", text: "読み込み中…" }));
  try {
    const files = await api("/api/content");
    if (!files.length) {
      filesList.replaceChildren(
        el("li", { class: "empty", text: "content/ に .md / .txt を置くとここに出ます" })
      );
      return;
    }
    filesList.replaceChildren(
      ...files.map((f) =>
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
  } catch (err) {
    filesList.replaceChildren(el("li", { class: "empty", text: `一覧を取得できません: ${err.message}` }));
  }
}

function markCurrentFile(path) {
  for (const btn of filesList.querySelectorAll("button")) {
    if (path && btn.dataset.path === path) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
  }
}

async function openContent(path) {
  try {
    const res = await api(`/api/content/${path.split("/").map(encodeURIComponent).join("/")}`);
    setSource({ text: res.text, kind: "auto", filename: res.name, contentPath: path });
    markCurrentFile(path);
  } catch (err) {
    doc.innerHTML = `<p class="status error">${esc(err.message)}</p>`;
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
  setSource({ text: await file.text(), kind: "auto", filename: file.name });
  markCurrentFile(null);
});

// ------------------------------------------------------------------ 起動

paintEntryCount($("count"));
loadFileList();
