// 辞書の見方のタブ。**一覧も 1 つの見方**として同じ列に並べる。
//
// 以前は topbar が「辞書」と「相関図」に割れていて、**同じ辞書を別の見方で出して
// いるだけ**なのに別の場所のように見えていた。総称も苦しくなっていた ——
// 地図や年表まで「相関図」と呼ぶことになるが、地図は「どこ」、年表は「いつ」で、
// 関係の図ではない。**タブにすると総称が要らなくなる**（タブの名前が中身を言う）。
//
// **ここが 6 つの正**（`graph.js` にも `glossary.js` にも写しを作らない）。
// 見せ方を足すときはここに 1 行足せば、両方の画面のタブに出る。
//
// **列に並べるのは「同じ範囲を別の見方で出すもの」だけ。** 地図は絵 1 枚ぶんの
// 見方なので、列からは外して ⋯ と用語ページの 🗺 から開く（`OFF_TAB_MODES`）。
import { el } from "./base.js";

//: 一覧のタブ。**図と同じ列に並ぶが、モードではない**（`MODES` に混ぜないこと ——
//: `graph.js` が「読めない値は段の図に落とす」の判定にそのまま使っている）
export const LIST_TAB = "list";

//: 図の見せ方。layered = 段の図（既定） / fabric = 交差しない図 / matrix = 行列 /
//: ego = 1 語を中心にした図 / timeline = 時系列（読む順・作中の時刻）
//:
//: **どれも「辞書を丸ごと別の見方で出す」もの**（中心の図だけは 1 語の近所だが、
//: 出していない語を数えて返し、押せばどこへでも辿れる）。並べる範囲が同じなので、
//: 1 つの列にできる
export const MODES = ["layered", "fabric", "matrix", "ego", "timeline"];

//: 列には並べないが、`/graph` の見せ方としては生きているもの。
//:
//: **地図をタブから外したのは、範囲が違うから。** 地図が出すのは**絵 1 枚の上**で、
//: 辞書に絵が何枚あってもそのうち 1 枚しか出せない（残りは選択と注意書きでしか
//: 分からなかった）。タブ列に並べると「辞書ぜんぶの見方」に見えるが、実際は
//: 「この絵の見方」で、**用語ページの 🗺 タブと同じ範囲**のもの。
//: 入口は 図の ⋯ の「🗺 地図」と、用語ページの 🗺、そして `?mode=map`。
//: **`MODES` に戻さないこと** —— 戻すと、絵が 1 枚も無い辞書でもタブが 1 つ増え、
//: 「押しても段の図に落ちるタブ」が常設になる
export const OFF_TAB_MODES = ["map"];

//: `/graph` が受け付ける見せ方ぜんぶ（URL の `?mode=` と `pickMode()` はこちらで見る）
export const ALL_MODES = [...MODES, ...OFF_TAB_MODES];

//: 覚えておく鍵。**覆いは何度でも開き直される**ので、毎回選び直させない。
//: 一覧はここに入れない —— topbar の「辞書」は一覧の入口のままにしておきたい
export const MODE_KEY = "glosspop.graphMode";

//: タブと、注意書きに出す短い名前。**`<option>` 時代の説明つきの文言に戻さないこと**
//: —— タブは折り返さないので、「地図（座標のある語）」の長さを 6 つ並べると
//: 行が 2 段になる。説明は `MODE_HINTS`（`title`）へ
export const MODE_WORDS = {
  layered: "段の図", fabric: "交差しない図", matrix: "行列",
  ego: "中心の図", timeline: "時系列", map: "地図",
};

export const MODE_HINTS = {
  [LIST_TAB]: "用語をカードで並べる（探す・登録する）",
  layered: "上下を段で表した既定の図",
  fabric: "用語が横線、関係が縦線。線が交差しない",
  matrix: "行が「から」、列が「へ」。書いていない組が空きマスで見える",
  ego: "1 語のまわり 2 つ先まで。規模に依らない",
  timeline: "読む順 / 作中の時刻の順に並べる",
  map: "座標を書いた語を絵の上に置く",
};

/** 覚えている見せ方。**読めない値は段の図に落とす**（既定を壊さない）。 */
export function rememberedMode() {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    return MODES.includes(saved) ? saved : "layered";
  } catch {
    return "layered";
  }
}

/**
 * 見せ方を覚える。**押した ＝ 選んだ**ので、画面をまたいでも同じ扱いにする。
 *
 * **列に無いもの（地図）は覚えない。** 覚えると、次に「図」を開いたときに
 * どのタブも選ばれていない状態で始まる —— 地図は**その絵を名指しして開くもの**
 * （⋯ か用語ページの 🗺 か `?map=`）なので、開き方のほうが必ず手元にある。
 */
export function rememberMode(value) {
  if (!MODES.includes(value)) return;
  try {
    localStorage.setItem(MODE_KEY, value);
  } catch {
    /* 使えない環境でも選べること自体は動く */
  }
}

/**
 * タブ列を ``host`` に描く。**同じ画面の中で切り替わるものはボタン、画面をまたぐ
 * ものはリンク**にする。
 *
 * リンクにしてあるのは 2 つの理由:
 *
 * - 覆い (`overlay.js`) が `<a href>` を拾って**重ねたまま**開いてくれる
 *   （URL と履歴もそちらが面倒を見る）。ページとして開いていれば普通の遷移になる
 * - **`onPick` は同じデータの描き替えにだけ使う。** 図から図はサーバへ行き直さない
 *   という約束があるので、そこをリンクにするとタブを押すたびに `/api/graph` を
 *   引き直すことになる
 *
 * 一覧から図へ渡るときは、**行く前に見せ方を覚える**（`?mode=` を付けない）。
 * `?mode=` は「リンクが見せ方まで名指ししてきた」印で、**覚えているほうを
 * 書き換えず注意書きを出す**という別の意味を持っている —— 自分でタブを押したのに
 * 「開いたリンクの指定で…にしています」と言われるのはおかしい。
 */
export function paintDictTabs(host, { current, onPick, scope = null }) {
  //: **絞り込みはタブをまたいでも持っていく。** 「文学の一覧」から図へ渡ったら
  //: 「文学の図」が出てほしい —— ここで落とすと、絞り込み直す手間が毎回いる。
  //: 用語ページの「カテゴリ全体の図 →」を消せたのはこれがあるから
  //: （**全体への出口は、その語の関係の下ではなくパンくずの側**）
  const query = new URLSearchParams();
  if (scope?.category) {
    query.set("category", scope.category);
    if (scope.scope) query.set("scope", scope.scope);
  }
  const qs = query.toString() ? `?${query}` : "";

  // **いま出しているものが列に無いなら、その間だけ末尾に足す**（地図がそれ）。
  // 足さないと、選ばれたタブが 1 つも無い列になり、**どこに居るのかも、どうやって
  // 戻るのかも画面に出ない**。列そのものを隠す手もあるが、それだと戻り道が消える
  const shown = [LIST_TAB, ...MODES];
  if (!shown.includes(current)) shown.push(current);

  const tabs = shown.map((value) => {
    const selected = value === current;
    const label = value === LIST_TAB ? "一覧" : MODE_WORDS[value];
    const common = {
      role: "tab",
      "data-mode": value,
      "aria-selected": selected ? "true" : "false",
      tabindex: selected ? "0" : "-1",
      title: MODE_HINTS[value] || "",
      text: label,
    };
    if (selected) return el("button", { ...common, type: "button" });
    if (value === LIST_TAB) return el("a", { ...common, href: `/glossary${qs}` });
    if (current === LIST_TAB) {
      // 一覧 → 図。**行く前に覚える**（押した ＝ 選んだ）
      return el("a", {
        ...common,
        href: `/graph${qs}`,
        onclick: () => rememberMode(value),
      });
    }
    // 図 → 図。**サーバへ行き直さない**（同じデータの描き替え）
    return el("button", { ...common, type: "button", onclick: () => onPick?.(value) });
  });

  host.replaceChildren(...tabs);
  return tabs;
}

/**
 * 左右キーで辿れるようにする（`role="tablist"` の作法）。**1 回だけ仕掛ける。**
 *
 * **矢印では切り替えない**（焦点が移るだけ。決めるのは Enter / Space）。
 * 列にリンクが混ざっているので、矢印で切り替える形にすると**辿っただけで
 * ページが変わる**。
 */
export function installTabKeys(host) {
  host.addEventListener("keydown", (ev) => {
    const step = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    const all = [...host.querySelectorAll("[data-mode]")];
    const at = all.indexOf(document.activeElement);
    if (at < 0) return;
    ev.preventDefault();
    all[(at + step + all.length) % all.length].focus();
  });
}
