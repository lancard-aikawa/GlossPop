// 読書位置の記憶。長い本を開き直したとき、前回の続きから出す。
//
// **覚えるのは名前のある文書だけ。** フォルダから開いたファイル（フォルダ + 相対パス）
// と URL には安定した名前があるが、「ファイルを開く」「ドロップ」「貼り付け」には無い
// —— ブラウザはファイルの絶対パスを渡さないので、ファイル名しか手掛かりが残らない。
// 無いものに名前を付けて覚えると、次に開いた同名の別ファイルを同じ鍵で上書きする。
// 文書内リンクを辿れないのと同じ制約から来ている。
//
// **位置は px ではなく段落の番号で持つ。** 窓の幅・文字サイズ・表示オプションで px は
// 動くが、段落の並びは動かない。読み上げ (`speech.js`) が段落単位なのとも揃う。

const KEY = "glosspop.reading";

//: 覚えておく文書の数。読まなくなったものをいつまでも持たない
const MAX_ENTRIES = 200;

//: スクロールが止まってから書くまでの待ち。1 スクロールごとに書かない
const SAVE_DELAY = 500;

//: ここより手前は「まだ読み始めていない」とみなして覚えない。
//: 開いただけの文書が全部「続きから」になると、案内がただの雑音になる
const MIN_BLOCK = 2;

/**
 * 文書を指す鍵。覚えない文書では ``null``。
 *
 * 区切りに ``<>`` を使うのは、Windows のパスにも URL にも現れない文字だから
 * （辞書側で ref をつなぐ鍵と同じ理由）。
 */
export function keyFor(source, root = "") {
  if (!source) return null;
  if (source.url) return `url<>${source.url}`;
  if (source.contentPath) return `file<>${root}<>${source.contentPath}`;
  return null;
}

function loadAll() {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) || "{}");
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

function saveAll(data) {
  try {
    localStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    /* プライベートモード等で書けなくても、読むことはできる */
  }
}

export function recall(key) {
  const saved = key ? loadAll()[key] : null;
  return saved && Number.isInteger(saved.block) ? saved : null;
}

export function remember(key, block, total) {
  if (!key) return;
  const data = loadAll();
  data[key] = { block, total, at: Date.now() };
  const keys = Object.keys(data);
  if (keys.length > MAX_ENTRIES) {
    keys
      .sort((a, b) => (data[a].at || 0) - (data[b].at || 0))
      .slice(0, keys.length - MAX_ENTRIES)
      .forEach((k) => delete data[k]);
  }
  saveAll(data);
}

export function forget(key) {
  if (!key) return;
  const data = loadAll();
  if (!(key in data)) return;
  delete data[key];
  saveAll(data);
}

/** いま画面の上端にある段落の番号。 */
function topBlock(container, doc) {
  const top = container.getBoundingClientRect().top;
  const blocks = doc.children;
  for (let i = 0; i < blocks.length; i++) {
    // 下端が画面の上端より下にある最初の要素 = いま読んでいるところ
    if (blocks[i].getBoundingClientRect().bottom > top + 4) return i;
  }
  return Math.max(0, blocks.length - 1);
}

/**
 * スクロールを見張って位置を覚える。
 *
 * 文書を切り替えるときは ``switchTo()`` を **本文を描き替える前に**呼ぶこと。
 * 中で今の位置を書き出すので、描き替えたあとに呼ぶと新しい本文の位置を
 * 古い文書の鍵で保存してしまう。
 */
export function createTracker({ container, doc }) {
  let key = null;
  let timer = null;
  let restoring = false;

  function flush() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (!key || restoring) return;
    const total = doc.children.length;
    if (!total) return;
    const block = topBlock(container, doc);
    // 先頭まで戻したら覚えていたものを捨てる（「続きから」が出続けない）
    if (block < MIN_BLOCK) forget(key);
    else remember(key, block, total);
  }

  container.addEventListener(
    "scroll",
    () => {
      if (!key || restoring || timer) return;
      timer = setTimeout(flush, SAVE_DELAY);
    },
    { passive: true }
  );
  // 窓を閉じる / 別ページへ移るときは待たずに書く
  window.addEventListener("pagehide", flush);

  return {
    /** いまの位置を書き出してから鍵を差し替える。本文を描き替える前に呼ぶ。 */
    switchTo(nextKey) {
      flush();
      key = nextKey;
    },

    /** 覚えている位置へ寄せる。戻したなら ``{block, total}``、戻さなければ ``null``。 */
    restore() {
      const saved = recall(key);
      const blocks = doc.children;
      if (!saved || !blocks.length) return null;
      const block = Math.min(saved.block, blocks.length - 1);
      if (block < MIN_BLOCK) return null;
      // 復元で起きるスクロールを書き戻さない。scroll イベントは
      // scrollIntoView のあと非同期に来るので、少しだけ間を置いて戻す
      restoring = true;
      blocks[block].scrollIntoView({ block: "start" });
      setTimeout(() => {
        restoring = false;
      }, 150);
      return { block, total: blocks.length };
    },

    /** 先頭へ戻し、覚えていた位置を捨てる。 */
    reset() {
      forget(key);
      container.scrollTo({ top: 0 });
    },

    /** 新しい文書を先頭から出す（前の文書のスクロール位置を引きずらない）。 */
    toTop() {
      container.scrollTo({ top: 0 });
    },
  };
}
