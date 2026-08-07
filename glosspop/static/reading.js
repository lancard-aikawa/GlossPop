// 「いまどこまで読んだか」をビューアから相関図へ渡す細い口。
//
// **単位を揃えようとしないこと。** 読書位置は `progress.js` が**描き終わった本文の
// ブロックの添字**で持ち、サーバの位置は**文字位置**（`timeline.py`）。片方をもう
// 片方に換算する道は無い —— 描画のブロックは Markdown の構文も章見出しの挿入も
// 通ったあとの形なので、文字数が一致しない。**近いはずの数字で埋めると、
// 「ここまで読んだ」が静かに嘘になる**（ネタバレ抑止に使う値でそれは許されない）。
//
// **代わりに本文のリンクそのものを使う。** 自動リンク (`a.gloss-link`) は
// `Linker` が出したものなので、**どの語が出てきたかの判断が二重にならない**
// （素の部分一致に戻さない、と決めてあるのと同じ話）。読み終えたブロックの中に
// リンクがあれば「その語は出てきた」で、これは画面に見えている事実そのもの。
//
// **ビューアが持っているものを相関図が読む**形にしてあるのは、覆いが何度でも
// 開き直されるため —— 開いた時点の値を渡すのではなく、**そのとき呼ぶ**。

//: ビューアが登録する「いまの読書位置」を返す関数。ビューアを開いていなければ
//: `null` のまま（`/graph` を直接開いたときは、この機能そのものが出ない）
let provider = null;

/** ビューアから登録する。**渡すのは値ではなく関数**（開くたびに計算し直す）。 */
export function provideReading(fn) {
  provider = typeof fn === "function" ? fn : null;
}

/** いまの読書位置。読んでいるものが無ければ `null`。 */
export function readingNow() {
  try {
    return provider ? provider() : null;
  } catch {
    return null;                    // 読めなくても図そのものは出す
  }
}

/**
 * **いま画面に出ている最後の段落**の番号。
 *
 * `progress.js` の `topBlockOf()` とは**別の問い**なので分けてある（写しではない）:
 *
 * - あちらは**再開する位置** —— 次に開いたときここから読む、なので**上端**
 * - こちらは**読んだところ** —— 画面に出ているものはもう目に入っている、なので**下端**
 *
 * 上端で切ると、開いた直後は「まだ何も出てきていない」になる ——
 * **画面にその語が見えているのに図には出ない**、が起きて説明が付かない。
 */
export function readBlock(container, doc) {
  const bottom = container.getBoundingClientRect().bottom;
  const blocks = doc.children;
  for (let i = blocks.length - 1; i >= 0; i--) {
    // 上端が画面の下端より上にある最後の要素 = ここまでは目に入っている
    if (blocks[i].getBoundingClientRect().top < bottom) return i;
  }
  return 0;
}

/** ``/glossary/<ref>`` から ref を取り出す。それ以外の行き先は `null`。 */
function refOf(href) {
  try {
    const path = new URL(href, location.origin).pathname;
    if (!path.startsWith("/glossary/")) return null;
    return decodeURIComponent(path.slice("/glossary/".length));
  } catch {
    return null;
  }
}

/**
 * ``block`` 番目まで読んだ時点で**出てきた語**を集める。
 *
 * **決めきれない語は入れない。** 同じ表記が複数のカテゴリにあるとき、リンクの
 * 行き先は検索ページになる（`data-count` が 2 以上）—— どのエントリのことかは
 * 画面からは決まらないので、**寄せずに数える**（`relations.resolve()` が
 * 絞りきれないときに黙って寄せないのと同じ約束）。**入れるほうへ倒すと、
 * まだ会っていない人物の関係が出る**ので、伏せる側へ倒す。
 */
export function seenUpTo(docNode, block) {
  const blocks = [...(docNode?.children || [])];
  const refs = new Set();
  let undecided = 0;
  const last = Math.min(block, blocks.length - 1);
  for (let i = 0; i <= last; i++) {
    for (const link of blocks[i].querySelectorAll("a.gloss-link")) {
      if (Number(link.dataset.count || "1") > 1) {
        undecided++;
        continue;
      }
      const ref = refOf(link.getAttribute("href") || "");
      if (ref) refs.add(ref);
    }
  }
  return { refs, undecided, block: last, blocks: blocks.length };
}
