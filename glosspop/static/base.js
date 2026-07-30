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
