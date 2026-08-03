// 共通ユーティリティ

export async function api(path, { method = "GET", body, signal } = {}) {
  const init = { method, signal, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `${res.status} ${res.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

// ネタバレ設定 (AI にどこまで読ませるか) は登録ダイアログと抽出ダイアログで共用する
const SPOILER_KEY = "glosspop.spoiler";

/** 既定値: 前回の選択 (localStorage) > サーバの設定 > full。 */
export async function defaultSpoiler() {
  try {
    const saved = localStorage.getItem(SPOILER_KEY);
    if (saved) return saved;
  } catch {
    /* 読めなくてもサーバ既定に落ちるだけ */
  }
  try {
    return (await api("/api/health")).spoiler_default || "full";
  } catch {
    return "full";
  }
}

export function rememberSpoiler(value) {
  try {
    localStorage.setItem(SPOILER_KEY, value);
  } catch {
    /* 保存できなくてもその回の選択は効く */
  }
}

// 表示テーマ。**キー名は各 HTML の head にあるインライン script と同じもの。**
// あちらは描画前に当てるためだけの 3 行で、ここが本体
export const THEME_KEY = "glosspop.theme";
export const THEMES = ["system", "light", "dark"];

/** いまの設定。既定は OS に合わせる。 */
export function currentTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (THEMES.includes(value)) return value;
  } catch {
    /* 読めなければ OS に合わせる */
  }
  return "system";
}

/** テーマを当てて記憶する。``system`` なら属性を外して OS の設定に戻す。 */
export function applyTheme(value) {
  const theme = THEMES.includes(value) ? value : "system";
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* 保存できなくてもその画面では効く */
  }
  return theme;
}

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child);
  }
  return node;
}

//: http(s) の URL だけをリンクにする。javascript: などは href に載せない
const HTTP_URL = /^https?:\/\/[^\s<>"]+$/i;

export function isHttpUrl(value) {
  return HTTP_URL.test(String(value ?? "").trim());
}

/** 別タブで開く外部リンク。opener を渡さない。 */
export function externalLink(href, text) {
  return el("a", {
    class: "ext-link",
    href,
    target: "_blank",
    rel: "noreferrer noopener",
    title: href,
    text: text ?? href,
  });
}

/**
 * 出典を表示する要素を返す。URL なら別タブで開くリンク、それ以外はただの文字列。
 * ファイル名や「辞書: 冪等」のような出典もあるので、URL のときだけリンクにする。
 */
export function sourceNode(source, { label = "出典: " } = {}) {
  const value = String(source ?? "").trim();
  if (!value) return null;
  if (!isHttpUrl(value)) return el("span", { text: `${label}${value}` });
  return el("span", {}, [el("span", { text: label }), externalLink(value)]);
}

export function setStatus(node, message, kind = "") {
  if (!node) return;
  node.textContent = message || "";
  node.className = `status${kind ? " " + kind : ""}`;
}

export async function paintEntryCount(node) {
  if (!node) return;
  try {
    const health = await api("/api/health");
    node.textContent = `${health.entry_count} 語登録`;
    node.title = `辞書: ${health.glossary_dir}`;
    return health;
  } catch {
    node.textContent = "";
  }
}
