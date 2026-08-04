// ビューアの上に重ねる覆い。辞書・用語・相関図・点検はここに描く。
//
// **ビューアは下で生きたまま**にするのが目的。ページとして開き直していた頃は、
// 相関図から戻るだけで本文を取り直して描き直しており、実測 149 ms（39,000 字）
// かかっていた。長い作品ではサーバの render だけで 159 ms（353,000 字）、
// これにブラウザの描画が乗るので往復 0.5 秒級になる。**重ねれば 0 ms**で、
// 読書位置・スクロール・読み上げの状態もそのまま残る（→ docs/design-notes.md）。
//
// ページ (`/glossary` など) も残してある。直接開かれた URL・ブックマーク・
// 別窓のためで、**中身の出どころは同じ**（各モジュールの `mount()`）。
import { dictionaryRevision } from "./popup.js";

//: 覆いに出せるもの。上から順に見て、最初に当たったものを使う
//: （`/glossary/<カテゴリ>/<slug>` が `/glossary` に食われないよう、細かい順）
const ROUTES = [
  {
    // **深さで見分けない。** ローカル辞書の ref は `.local/<カテゴリ>/<slug>` で
    // 1 段深いので、`/glossary/x/y` の形だけを拾うと**フォルダの辞書の用語ページ
    // だけページ移動になる**（見た目は同じなので気付きにくい）
    match: /^\/glossary\/.+$/,
    load: () => import("./entry.js"),
    nav: "/glossary",
    title: "用語",
    mount: (mod, host, url) => mod.mount(host, { path: url.pathname }),
  },
  {
    match: /^\/glossary\/?$/,
    load: () => import("./glossary.js"),
    nav: "/glossary",
    title: "辞書",
    mount: (mod, host, url) => mod.mount(host, { search: url.search }),
  },
  {
    match: /^\/graph\/?$/,
    load: () => import("./graph.js"),
    nav: "/graph",
    title: "相関図",
    mount: (mod, host, url) => mod.mount(host, { search: url.search, embed: true }),
  },
  {
    match: /^\/doctor\/?$/,
    load: () => import("./doctor.js"),
    nav: "/graph",
    title: "点検",
    mount: (mod, host) => mod.mount(host),
  },
];

function routeFor(url) {
  return ROUTES.find((r) => r.match.test(url.pathname)) || null;
}

let node = null;
let body = null;
let hooks = {};
let openUrl = "";
//: 覆いを開いた時点の辞書の版。閉じるときに増えていたら本文を描き直す
//: （増えていなければリンクも吹き出しも変わらないので、描き直さない）
let revisionAtOpen = 0;
//: 覆いを開く前のタブの題。閉じたら戻す（戻さないと、読んでいる本の題が
//: 「結果整合性 — GlossPop」のまま残る）
let titleBefore = "";

function build() {
  if (node) return node;
  node = document.createElement("div");
  node.className = "overlay";
  node.hidden = true;
  node.innerHTML = `
    <div class="overlay-bar">
      <button type="button" class="ghost" data-ref="close">✕ 読書に戻る</button>
      <span class="spacer"></span>
      <span class="hint" data-ref="where"></span>
    </div>
    <div class="page"><div class="page-inner" data-ref="body"></div></div>
  `;
  body = node.querySelector("[data-ref=body]");
  node.querySelector("[data-ref=close]").addEventListener("click", () => close());
  document.body.append(node);
  return node;
}

/** topbar のすぐ下から下端までを覆う（topbar は使えるままにしておく）。 */
function fit() {
  const bar = document.querySelector(".topbar");
  if (node && bar) node.style.top = `${Math.round(bar.getBoundingClientRect().height)}px`;
}

function paintNav(nav) {
  for (const a of document.querySelectorAll(".topnav a")) {
    const href = a.getAttribute("href");
    if (nav && href === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (!nav) {
    document.querySelector('.topnav a[href="/"]')?.setAttribute("aria-current", "page");
  }
}

/** 覆いを開く。``push`` が偽なら履歴を積まない（「戻る」で開き直したとき）。 */
export async function open(href, { push = true } = {}) {
  const url = new URL(href, location.href);
  const route = routeFor(url);
  if (!route) {                       // 知らない行き先はページ移動に任せる
    location.href = url.href;
    return false;
  }
  build();
  fit();
  const wasOpen = !node.hidden;
  if (!wasOpen) {
    revisionAtOpen = dictionaryRevision();
    titleBefore = document.title;
  }
  node.hidden = false;
  node.scrollTop = 0;
  document.body.classList.add("overlaid");
  node.querySelector("[data-ref=where]").textContent = route.title;
  paintNav(route.nav);
  document.title = `GlossPop — ${route.title}`;
  openUrl = url.pathname + url.search;
  if (push) history.pushState({ overlay: openUrl }, "", openUrl);

  body.replaceChildren();
  body.setAttribute("aria-busy", "true");
  try {
    const mod = await route.load();
    await route.mount(mod, body, url);
  } catch (err) {
    body.replaceChildren();
    const p = document.createElement("p");
    p.className = "status error";
    p.textContent = `開けません: ${err.message}`;
    body.append(p);
  } finally {
    body.removeAttribute("aria-busy");
  }
  return true;
}

/**
 * 覆いを閉じてビューアに戻る。
 *
 * **辞書が変わっていたときだけ本文を描き直す。** 毎回描き直すと、重ねた意味
 * （戻りが 0 ms）が無くなる。判断は `dictionaryRevision()` で、登録・編集・
 * 削除・統合はすべて `invalidatePopupCache()` を通るので取りこぼさない。
 */
export function close({ push = true } = {}) {
  if (!node || node.hidden) return;
  node.hidden = true;
  body.replaceChildren();
  document.body.classList.remove("overlaid");
  openUrl = "";
  paintNav("");
  if (titleBefore) document.title = titleBefore;
  hooks.onClose?.({ changed: dictionaryRevision() !== revisionAtOpen });
  if (push) history.pushState({ overlay: null }, "", hooks.viewerUrl?.() || "/");
}

export function isOpen() {
  return Boolean(node) && !node.hidden;
}

/**
 * ビューアに仕掛ける。
 *
 * `hooks.viewerUrl()` は閉じたときに戻す URL、`hooks.onClose()` は閉じたあとの
 * 後始末（変わっていれば本文を描き直す）、`hooks.onViewerLink(url)` は覆いの中から
 * ビューアを名指しで呼ぶリンク（用語ページの「初出へ」など）。
 */
export function installOverlay(options = {}) {
  hooks = options;
  build();
  addEventListener("resize", fit);

  // 覆いに出せる行き先へのリンクは、ページ移動ではなく重ねて開く。
  // **本物の `<a href>` のまま**にしてあるので、中クリックや Ctrl+クリックは
  // ブラウザに任せられる（覆いはこの窓の中の話でしかない）
  document.addEventListener("click", (ev) => {
    if (ev.defaultPrevented || ev.button !== 0) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const a = ev.target.closest?.("a[href]");
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;

    if (routeFor(url)) {
      ev.preventDefault();
      open(url.href);
      return;
    }
    // ビューア宛て（`/` と `/?open=...`）。覆いを閉じて、指定があればそれを開く
    if (url.pathname === "/") {
      ev.preventDefault();
      close();
      hooks.onViewerLink?.(url);
    }
  });

  addEventListener("popstate", () => {
    const url = new URL(location.href);
    if (routeFor(url)) open(url.href, { push: false });
    else close({ push: false });
  });

  addEventListener("keydown", (ev) => {
    // ダイアログが開いているときは、そちらの Esc（閉じる）を邪魔しない
    if (ev.key !== "Escape" || !isOpen()) return;
    if (document.querySelector("dialog[open]")) return;
    close();
  });
}
