// 新しい版が出ていたら topbar に静かに出す。
//
// 押し付けない: 小さなリンクを 1 本置くだけで、モーダルも自動ダウンロードもしない。
// 失敗したら何も出さない（ネットが無い・GitHub が落ちている、は本体に関係が無い）。
//
// **外へ通信する唯一の経路**なので、⚙ から切れるようにしてある。
import { api, el } from "./base.js";

let state = null;

/** 直近の結果。⚙ が「更新の確認」の表示に使う。 */
export function lastResult() {
  return state;
}

export async function refreshUpdateNotice({ force = false } = {}) {
  const bar = document.querySelector(".topbar");
  if (!bar) return null;
  try {
    state = await api(`/api/update${force ? "?force=true" : ""}`);
  } catch {
    return null;      // 黙る
  }
  const existing = bar.querySelector("#update-notice");
  if (existing) existing.remove();
  if (!state.newer) return state;

  const notice = el("a", {
    id: "update-notice",
    class: "update-notice",
    href: state.url,
    target: "_blank",
    rel: "noreferrer noopener",
    title: `いま ${state.current} を使っています。${state.latest} の内容を見る`,
    text: `${state.latest} が出ています`,
  });
  const meta = bar.querySelector(".meta");
  if (meta) bar.insertBefore(notice, meta);
  else bar.append(notice);
  return state;
}

// 画面を開いたときに 1 回だけ。サーバ側が 1 日 1 回までに絞っているので、
// ページを開き直しても GitHub は叩かれない
refreshUpdateNotice();
