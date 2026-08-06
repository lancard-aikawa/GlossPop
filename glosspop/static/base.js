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

// 文字の大きさ。**キー名も値も各 HTML の head にあるインライン script と同じもの**
// （テーマと同じ形）。あちらは描画前に当てるためだけで、ここが本体。
//
// **周りの px を直して回らないこと。** 大きさの正は style.css の `--fs-base` 1 つで、
// ほかの字はそこから比で作ってある。ここが決めるのはどの base を当てるかだけ。
export const FONT_KEY = "glosspop.fontSize";
export const FONT_SIZES = ["small", "medium", "large", "xlarge"];

/** いまの設定。既定は ``medium``（＝これまでと同じ大きさ）。 */
export function currentFontSize() {
  try {
    const value = localStorage.getItem(FONT_KEY);
    if (FONT_SIZES.includes(value)) return value;
  } catch {
    /* 読めなければ既定の大きさ */
  }
  return "medium";
}

/** 大きさを当てて記憶する。``medium`` なら属性を外す（テーマの ``system`` と同じ）。 */
export function applyFontSize(value) {
  const size = FONT_SIZES.includes(value) ? value : "medium";
  if (size === "medium") delete document.documentElement.dataset.fontsize;
  else document.documentElement.dataset.fontsize = size;
  try {
    if (size === "medium") localStorage.removeItem(FONT_KEY);
    else localStorage.setItem(FONT_KEY, size);
  } catch {
    /* 保存できなくてもその画面では効く */
  }
  return size;
}

// ------------------------------------------------------------- 表示オプション
//
// 本文の見せ方の設定。**設定ダイアログに置いてあるが、効くのはビューアの本文。**
// ページをまたいで持つ必要があるので localStorage に入れ、変わったことは
// `subscribe` で伝える（`storage` イベントは**別のタブ**でしか飛ばないので、
// 同じページに居るビューアには届かない）。テーマと同じで、押した瞬間に効かせる。

export const FIRST_ONLY_KEY = "glosspop.firstOnly";

const firstOnlyWatchers = new Set();

/** 各用語の最初の 1 回だけリンクするか。既定は偽（全部リンクする）。 */
export function firstOnly() {
  try {
    return localStorage.getItem(FIRST_ONLY_KEY) === "1";
  } catch {
    return false;
  }
}

/** 設定を書き換えて、見ている画面に知らせる。 */
export function setFirstOnly(value) {
  const on = Boolean(value);
  try {
    if (on) localStorage.setItem(FIRST_ONLY_KEY, "1");
    else localStorage.removeItem(FIRST_ONLY_KEY);
  } catch {
    /* 保存できなくてもその画面では効く */
  }
  for (const fn of firstOnlyWatchers) fn(on);
  return on;
}

/** 変わったら呼ぶ。戻り値を呼ぶと外れる。 */
export function watchFirstOnly(fn) {
  firstOnlyWatchers.add(fn);
  return () => firstOnlyWatchers.delete(fn);
}

// 関係の上下。**値は `models.RANKS` と同じ文字列**でないとサーバが弾く。
// 用語ページ・関係の下書き・相関図の 3 つが同じ表示をするので、正はここ 1 つ。
// **すべて「自分から見て相手がどうか」** の向きで読む（向きの基準は 1 つに固定）
export const RANK_OPTIONS = [
  ["", "上下は指定しない"],
  ["上", "相手が上"],
  ["下", "相手が下"],
  ["対等", "対等"],
];
export const RANK_MARK = { 上: "▲ 相手が上", 下: "▼ 相手が下", 対等: "＝ 対等" };

/**
 * 関係の一言。相互で逆向きの言葉があれば「A ⇄ B」にする。
 *
 * 相関図の 3 つの見せ方が同じ形で出すので、**正はここ 1 つ**。
 */
export function relationWords(edge) {
  return edge.mutual && edge.back && edge.back !== edge.label
    ? `${edge.label} ⇄ ${edge.back}`
    : edge.label || "";
}

/**
 * 関係 1 本を 1 行で説明する。吹き出しと、図の下の枠に出す文。
 *
 * **一言は切らずに全部入れる。** 図の中では場所が無くて切ったり畳んだりして
 * いるので（縦書きの 12 字、置き場所の無い一言）、**全文が読める場所がここ
 * しかない**。3 つの見せ方で同じ文にするために正をここへ置く。
 */
export function describeRelation(edge, { from = "", to = "" } = {}) {
  const bits = [];
  if (from && to) bits.push(`${from} ${edge.mutual ? "⇄" : "→"} ${to}`);
  const words = relationWords(edge);
  if (words) bits.push(words);
  bits.push(edge.mutual ? "相互" : "一方的");
  if (edge.rank) bits.push(RANK_MARK[edge.rank] || `相手が${edge.rank}`);
  if (edge.reveal) bits.push(`判明: ${edge.reveal}`);
  return bits.join(" / ");
}

/** 用語 1 つを 1 行で説明する（図の下の枠に出す文）。 */
export function describeNode(node) {
  if (node.missing) return `${node.term} — まだ登録されていません（押すと辞書で探せます）`;
  return [node.term, node.path_label, node.summary].filter(Boolean).join(" — ");
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

const SVG_NS = "http://www.w3.org/2000/svg";

/** ``el`` の SVG 版。名前空間が違うので `createElement` では作れない。 */
export function svgEl(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

/**
 * 文字列の描画幅の**見積もり**。全角は字送りとほぼ同じ、半角はその 57%。
 *
 * **実測より少し大きく出る**（11px で 1〜2 割）。小さく見積もると、重ならない
 * つもりで置いたものが実際には触れる。相関図が置き場所を決めるのに使う。
 */
export function estTextWidth(text, size = 11) {
  let w = 0;
  for (const ch of String(text || "")) {
    w += ch.codePointAt(0) > 0x2e7f ? size * 1.05 : size * 0.57;
  }
  return w;
}

//: 縦書きで**寝かせる**文字。長音符・括弧・ダッシュ・リーダは、立てたままだと
//: 横倒しに見える（「パートナー」の「ー」が横棒のまま残る）。SVG の `rotate`
//: 属性は 1 字ずつの回転なので、これだけを 90 度倒せる。
//: **`⇄` は入れないこと** —— この辞書では「相互」の意味で、倒すと `⇅`（上下）に
//: 見える。上下は `▲▼` と決めてあるので、別の意味に読めてしまう
const LAID_DOWN = new Set([...'ー〜～（）()「」『』【】［］[]｛｝{}〈〉《》―—–‐-…‥']);

/**
 * 縦書きにしたときに実際に積まれる文字。**高さの計算と描画で同じものを使う。**
 *
 * 空白を落としてから切ること —— 「教師 ⇄ 生徒」の空白は横書きのための区切りで、
 * 縦に積むと空の 1 行になるので落とす。落とす前に数えると、その 2 字ぶん早く
 * 切れて、切る必要のない一言にまで「…」が付く（実際にそうなった）。
 */
export function verticalChars(text, max = 0) {
  const chars = [...String(text || "")].filter((ch) => ch !== " ");
  return max && chars.length > max ? [...chars.slice(0, max), "…"] : chars;
}

/**
 * 一言を**縦書き**にする。文字を立てたまま 1 字ずつ積む。
 *
 * `writing-mode: vertical-rl` は SVG でも効くが、**`⇄` が `⇅` に回される**
 * （Unicode がこの記号を「縦では回す」に分類しているため。`text-orientation:
 * upright` を付けても Chrome では回った）。この辞書では `⇄` が「相互」、上下は
 * `▲▼` と決めてあるので、回った矢印は**別の意味に読める**。1 字ずつ置けば
 * 記号もそのままの向きで立つ。どのブラウザでも同じに出る、という利点もある。
 *
 * **下端を揃える**（上はぎざぎざ）。列の真上で終わるので、名前から目を落とした
 * ときにどの列の話なのかが切れずに繋がる。
 */
export function svgVerticalText(text, x, bottom, { max = 0, lineHeight = 12.5, className = "" } = {}) {
  const LINE_H = lineHeight;
  const chars = verticalChars(text, max);
  const top = bottom - (chars.length - 1) * LINE_H;
  return svgEl(
    "text",
    { x, y: top, class: className, "text-anchor": "middle" },
    chars.map((ch, i) => svgEl("tspan", {
      x,
      y: top + i * LINE_H,
      rotate: LAID_DOWN.has(ch) ? 90 : null,
      text: ch,
    }))
  );
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
