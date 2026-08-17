// 相関図。サーバはノードと辺だけを返し、**配置はここでやる**。
//
// 力学モデル (力の釣り合いを反復して解く) は使わない。カテゴリという階層が
// すでにあるうえ、`rank`（上下）で層が決まるので、決定的に置けば足りる。
// 乱数も収束待ちも無いぶん、同じ辞書なら毎回同じ絵になる。
//
// 段の**中**の並びだけは、隣接ノードの平均位置へ寄せる緩和を決まった回数まわして
// 選ぶ（`orderRows`）。回数で必ず止め、いちばん読みやすかった並びを `badness` で
// 選ぶだけなので、収束を待つ力学モデルとは別物（同じ辞書なら毎回同じ絵になる）。
//
// **入れ物は外から渡される**（`mount()`）。`/graph` を直接開いたときは
// そのページの器に、ビューアの上に重ねるときは覆いの器に、同じものを描く。
import {
  api, describeNode, describeRelation, el, estTextWidth, menuButton, paintEntryCount,
  RANK_OPTIONS, relationWords, setStatus, svgEl,
} from "./base.js";
import {
  ALL_MODES, installTabKeys, MODE_KEY, MODE_WORDS, MODES,
  paintDictTabs, rememberedMode, rememberMode,
} from "./dict-tabs.js";
import { levelsOf, seedOrder, splitLonely } from "./graph-model.js";
import { buildFabric } from "./fabric.js";
import { buildMatrix } from "./matrix.js";
import { buildTimeline } from "./timeline.js";
import { buildEgo, DEFAULT_DEPTH, DEPTHS, normalizeDepth } from "./ego.js";
import { buildMap, fitToKind, KIND_WORDS } from "./map.js";
import { saveGraph } from "./graph-export.js";
import { readingNow } from "./reading.js";
import { encodePath } from "./editor.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない。2 つに割ると、
//: 片方だけ直したときに「ページでは出るのに重ねると出ない」になる）
const TEMPLATE = `
<!-- **何を出している図なのかを必ず書く。** 書かないと、辞書全体の図を
     「開いている文書の図」だと思われる（その取り違えが元で下書きを
     ここから外した。→ docs/design-notes.md） -->
<div class="graph-head">
  <span class="hint" id="scopeNote"></span>
  <a class="btn" id="scopeAll" href="/graph" hidden>辞書全体を出す</a>
  <span class="spacer"></span>
  <span class="status" id="status"></span>
</div>
<!-- **一覧も同じ列に並ぶ。** 辞書と図は「同じものの別の見方」なので、topbar で
     場所を分けていたのをやめた（総称も要らなくなった —— 地図は「どこ」、年表は
     「いつ」で、まとめて「相関図」と呼ぶには無理があった）。中身は
     dict-tabs.js が作る（正はあちら。**ここに写しを置かないこと**）。
     **ここにバッククォートを書かないこと** —— テンプレート文字列の中なので、
     そこで文字列が切れて続きが式として読まれる（実際にここで踏んだ）。
     **タブを disabled にしないこと** —— 出せるかは描いてみないと分からないし、
     押せないタブには理由を書く場所が無い（→ CLAUDE.md の置き場所の表） -->
<div class="view-tabs" role="tablist" id="modeTabs" aria-label="辞書の見方"></div>
<div class="toolbar graph-toolbar">
  <!-- ここから **どのタブでも効くもの**。行の先頭に固めておくこと ——
       タブ専用のものと混ぜると、タブを変えるたびに並びがずれて
       「いま何ができるか」が読めなくなる -->
  <select id="category" class="auto-width" aria-label="カテゴリ"></select>
  <label class="check">
    <input type="checkbox" id="spoilers">
    <span>判明位置つきの関係も出す</span>
  </label>
  <!-- **ビューアで読んでいるときだけ出す。** 「どこまで読んだか」はビューアしか
       知らないので、相関図のページを直接開いたときは出せない（時系列・地図と同じ
       約束で、出しておいて効かないより出さないほうがまし。→ reading.js）。
       ここはテンプレート文字列の中なので、バッククォートを書かないこと -->
  <label class="check" id="readSoFarBox" hidden>
    <input type="checkbox" id="readSoFar">
    <span>ここまで読んだぶんだけ</span>
  </label>
  <!-- ここから **選んでいるタブ専用**。仕切りは出しているものがあるときだけ出す -->
  <span class="bar-sep" id="tabToolsSep" hidden></span>
  <!-- 並べる軸（時系列のときだけ）。**どちらで並べているかは必ず画面に出す** ——
       読む順（読者がいつ読めるか）と作中の時刻は別の時間で、一致しない -->
  <select id="timeAxis" class="auto-width" aria-label="並べる軸" hidden></select>
  <!-- 並べるもの（時系列のときだけ）。**関係と語は答える問いが違う** ——
       関係は「誰と誰がいつ繋がるか」、語は「何がいつ起きたか」。置き換えではない -->
  <select id="timeRows" class="auto-width" aria-label="並べるもの" hidden>
    <option value="node" title="1 行 1 語。何がいつ起きたか（相手は行のうしろに小さく出る）">語を並べる（年表）</option>
    <option value="edge" title="1 行 1 関係。誰と誰がいつ繋がるか">関係を並べる</option>
  </select>
  <!-- 何つ先まで（中心の図のときだけ）。**既定は 2 つ先**だが、「直接の関係だけ」も
       「もう一歩広く」も要る。深くすると規模に依らないという取り柄は薄れるので、
       出していない語の数は今までどおり凡例に出す -->
  <select id="egoDepth" class="auto-width" aria-label="何つ先まで" hidden></select>
  <!-- どの絵を出すか。**辞書に数枚ある**ので選べないと「ほかに 〇〇 があります」と
       書いておきながら行けない。地図のとき、絵が 2 枚以上あるときだけ出す -->
  <select id="mapPick" class="auto-width" aria-label="地図" hidden></select>
  <!-- **落ちているときも出す。** 絵が 1 枚も無いと段の図に落ちるので、ここを
       隠すと「最初の 1 枚を入れる」道が無くなる（鶏と卵になる） -->
  <button type="button" class="btn" id="mapEdit" hidden title="地図の絵を入れる / 消す">🖼 絵</button>
  <span class="spacer"></span>
  <!-- ⋯ の中身は mount() が menuButton() で作る（画像として保存 / 点検）。
       **ここへ「⬇ 画像」と形式の select を戻さないこと** —— 折り返したときに
       別々の行へ割れて、何の形式なのか読めなくなっていた（→ docs/ui-inventory.md）。
       **ここに「関係を下書き」も戻さない。** この図は辞書全体を出すのに、下書きは
       開いているフォルダの本文を読む。並べると「図に出ている語について探す」と
       読まれるが、実際に読むのは図ともビューアとも一致しない範囲だった
       （→ docs/design-notes.md） -->
  <span id="graphMenu"></span>
</div>
<!-- 時点のスライダ（地図で、関係に作中の時刻が書いてあるときだけ）。
     **一覧とは別の入れ物にする** —— 一覧は描き直すたびに作り直すので、同じ所に
     置くと**掴んでいる最中にスライダごと消えて**ドラッグが切れる（保存のたびに
     焦点が飛んでいたのと同じ形）。ここは時点の顔ぶれが変わったときだけ作り直す -->
<div class="map-layers" id="mapTimeBar" hidden></div>
<!-- 地図に出すものの一覧。**チェックを外したぶんは必ず数えて凡例に出す**
     （黙って欠けた図を出さない、という約束はここでも同じ）。地図のときだけ出す -->
<div class="map-layers" id="mapLayers" hidden></div>
<p class="notice" id="notes" hidden></p>
<!-- 拡大縮小のボタンは**図の上に重ねる**。ツールバーへ足すと、狭い画面で
     すでに折り返している行がもう 1 段増える -->
<div class="graph-stage">
  <div class="graph-canvas" id="canvas" tabindex="0"
       aria-label="相関図。ドラッグで動かし、ホイールで拡大縮小します">
    <p class="empty">読み込み中…</p>
  </div>
  <div class="graph-zoom" id="zoom" hidden>
    <button type="button" id="zoomOut" title="縮小 (−)" aria-label="縮小">−</button>
    <button type="button" id="zoomFit" title="全体を出す (0)">全体</button>
    <button type="button" id="zoomIn" title="拡大 (＋)" aria-label="拡大">＋</button>
  </div>
</div>
<!-- **常に場所を空けておく。** 出たり消えたりで高さが変わると、下の凡例ごと
     動いて読みにくい（空のときは案内文を出す）。ここに出すのは、乗せたもの／
     焦点が当たったものの説明 —— **図の中では切ったり畳んだりしている一言が、
     全文で読める唯一の場所**。ブラウザの吹き出しは**キーボードの焦点では
     出ない**ので、それだけに頼らない。
     （この TEMPLATE は JS のテンプレート文字列。**バッククォートを書かないこと**
     —— そこで文字列が切れて、続きが式として読まれる。実際に踏んだ） -->
<p class="graph-detail" id="detail"></p>
<p class="hint" id="legend"></p>

<!-- 地図の絵を入れる / 消す。**辞書に数枚ある**ので、顔と違って名前と一覧が要る -->
<dialog class="sheet" id="mapDialog">
  <div class="edge-editor">
    <header>
      <h2>地図の絵</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
    </header>
    <p class="hint">
      エントリに <code>map: 名前</code> と <code>pin</code> / <code>path</code> /
      <code>area</code> を書くと、その絵の上に出ます。
      <strong>絵には地名を入れないでください</strong> ——
      辞書の名前と二重になります（入ってしまっている絵は「名前を出す」を外して使います）。
      AI に描かせるなら <strong>2048px 以上</strong>で。拡大してもボケないのは SVG だけです。
    </p>
    <div data-ref="list"></div>
    <div class="cat-row">
      <input type="text" data-ref="name" class="cat-row-main"
             placeholder="絵の名前（例: 桶狭間）">
      <select data-ref="scope" class="auto-width" aria-label="保存先"></select>
      <input type="file" data-ref="file"
             accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml">
    </div>
    <footer>
      <span class="status" data-ref="status"></span>
      <div class="spacer"></div>
      <button type="button" class="primary" data-ref="save">入れる</button>
    </footer>
  </div>
</dialog>

<!-- 辺を押すと開く。点検ページと同じで、直すためにページを渡り歩かせない -->
<dialog class="sheet" id="edgeDialog">
  <div class="edge-editor">
    <header>
      <h2>関係を直す</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="close" aria-label="閉じる">✕</button>
    </header>
    <p class="rel-who" data-ref="who"></p>
    <div class="body">
      <label>相手
        <input type="text" data-ref="to" autocomplete="off"
               placeholder="用語名 または カテゴリ/slug"></label>
      <label>この語から見た一言
        <input type="text" data-ref="label" autocomplete="off" placeholder="例: 親友"></label>
      <label>逆から見た一言
        <input type="text" data-ref="back" autocomplete="off" placeholder="空なら一方的（→）"></label>
      <label>上下
        <select data-ref="rank" class="auto-width"></select></label>
      <label>判明する位置
        <input type="text" data-ref="reveal" autocomplete="off"
               placeholder="例: 第6章（書くと図では既定で伏せる）"></label>
      <!-- 作中の時刻。**判明する位置とは別の軸**（あちらは読者がいつ知るか）。
           並べ替えに使うのは**先頭の西暦だけ**で、うしろは書かれたまま出る -->
      <label>作中の時刻
        <input type="text" data-ref="when" autocomplete="off"
               placeholder="例: 1560-05-19 永禄三年五月十九日 / 16世紀 / 約1560（空でよい）"></label>
    </div>
    <footer>
      <button type="button" class="danger" data-ref="remove">削除</button>
      <span class="status" data-ref="status"></span>
      <span class="spacer"></span>
      <button type="button" data-ref="cancel">やめる</button>
      <button type="button" class="primary" data-ref="save">保存</button>
    </footer>
    <p class="hint">
      すべて「この語から見た相手」の向きで書きます。相手側に同じ関係を書く必要はありません。
    </p>
  </div>
</dialog>
`;

let canvas, notes, legend, statusNode, countNode, categorySelect, spoilerCheck, zoomBar;
let mapPick, mapLayers, mapEdit, mapDialog, timeAxisPick, timeRowsPick, mapTimeBar;
let egoDepthPick;
//: いま出している時点の顔ぶれ（絵 + 時刻の並び）。**同じなら作り直さない** ——
//: 掴んでいる最中にスライダが消えるとドラッグが切れる
let mapTimeKey = "";
//: 実際に描いた軸（選んだものと違うことがある。→ `axisFor`）
let drawnAxis = "";
let readSoFarCheck, readSoFarBox;
//: 「ここまで読んだぶんだけ」で伏せたことの断り書き（注意書きに出す）
let readingNote = "";
let detailNode;
//: 辞書の見方のタブ（一覧 ＋ 5 つの図。正は `dict-tabs.js`）と、
//: そのタブ専用の操作を仕切る線
let modeTabs, tabToolsSep;
//: タブを出しているか。**用語ページの中では出さない**（あちらが自分の列を持つ）。
//: 出していないときは「上のタブで戻せます」の断りも出さない —— 戻す先が無い
let showTabs = true;

//: 見せ方。layered = 段の図（既定） / fabric = 交差しない図 / matrix = 行列 /
//: ego = 1 語を中心にした図 / timeline = 時系列（文書を開いているときだけ）/
//: map = 地図（**タブ列には無い**。⋯ か用語ページの 🗺 か `?mode=map` から）。
//: **覚えておく** —— 覆いは何度でも開き直されるので、毎回選び直させない
//: （地図だけは覚えない。→ `dict-tabs.js` の `OFF_TAB_MODES`）。
//: **正は `dict-tabs.js`**（一覧のタブと同じ列に並ぶので、あちらが持っている）
let mode = "layered";

//: 時系列の**並べる軸**。read = 読者がその文書のどこで読めるようになるか
//: （`?doc=` が要る）、when = 作中でいつ起きたか（語か関係に `when` を書いたもの）。
//: **どちらも「時間」だが別物**なので、どちらで並べているかは必ず画面に出す。
//: **覚える** —— 覆いは何度でも開き直されるので、毎回選び直させない
const TIME_AXIS_KEY = "glosspop.graphTimeAxis";
//: 時系列で**並べるもの**。edge = 関係（1 行 1 本）/ node = 語（1 行 1 語 ＝ 年表）
const TIME_ROWS = ["edge", "node"];
const TIME_ROWS_KEY = "glosspop.graphTimeRows";
const TIME_AXES = ["read", "when"];
const AXIS_WORDS = { read: "読む順", when: "作中の時刻" };
let timeAxis = "read";
//: 選んだ軸が使えなかったので、もう片方で並べたか（**覚えているほうは
//: 書き換えない**。押しのけたことは注意書きに出す ——「地図が無いので段の図」と同じ）
let axisFellBack = "";
//: 時系列で並べるもの（覚える）。**軸とは別の選択** —— 軸は「どの時間か」、
//: こちらは「何を 1 行にするか」で、掛け合わせて 4 通りになる。
//: **既定は語（年表）。** 同じ事件が関係の本数だけ行に現れないぶん見通しがよく、
//: 「何がいつ起きたか」という時系列にいちばん近い問いに先に答えられる
//: （関係の並びは置き換えられていない —— タブの下の選択で切り替わる）
let timeRows = "node";

//: 中心の図で**何つ先まで**出すか。**覚える**（軸や見せ方と同じ約束で、覆いは
//: 何度でも開き直される）。**読めない値は既定 (2) に落とす**のは `ego.js` の仕事
const EGO_DEPTH_KEY = "glosspop.egoDepth";
let egoDepth = DEFAULT_DEPTH;

//: 2 つの ref をつないで組の鍵にするための区切り。カテゴリ名も slug も
//: "<" ">" を弾いているので、ref の中身と衝突しない
const SEP = "<>";

// ノードの箱。日本語は 1 文字がほぼ全角なので、文字数から幅を見積もる
const CHAR_W = 15;
const NODE_H = 40;
const NODE_MIN_W = 72;
const NODE_MAX_W = 220;
const GAP_X = 34;
const GAP_Y = 110;
const PAD = 40;

//: 関係の無い語をまとめる帯。段の外なので上下の意味を持たない
const LONELY_GAP = 56;
const LONELY_LINE_GAP = 18;

//: 段の中の並べ替えを何回まわすか。**回数で必ず止める**（収束は待たない）
const RELAX_PASSES = 12;

//: 読みにくさの重み。効くのは 交差 > 箱の貫通 > 線の長さ の順。
//: 追えなくなる原因は交差がいちばん大きく、長さは同点のときの決め手にしか使わない
const W_CROSS = 24;
const W_THROUGH = 30;
const W_LENGTH = 0.02;

//: SVG の要素づくりは base.js（fabric.js と同じものを使う）
const svg = svgEl;

function nodeWidth(term) {
  const chars = [...String(term || "")].length;
  return Math.max(NODE_MIN_W, Math.min(NODE_MAX_W, chars * CHAR_W + 24));
}

// --------------------------------------------------------------------------- //
// 配置
// --------------------------------------------------------------------------- //

/** 線分どうしが交わるか。端点を共有するものは呼ぶ側で除く。 */
function crosses(p1, p2, p3, p4) {
  const side = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  const d1 = side(p3, p4, p1);
  const d2 = side(p3, p4, p2);
  const d3 = side(p1, p2, p3);
  const d4 = side(p1, p2, p4);
  return d1 > 0 !== d2 > 0 && d3 > 0 !== d4 > 0;
}

/** 線分が箱を通り抜けるか。両端のノードは呼ぶ側で除く（端点は箱の外にある前提）。 */
function throughBox(a, b, box) {
  const l = box.x - box.w / 2;
  const r = box.x + box.w / 2;
  const t = box.y - box.h / 2;
  const u = box.y + box.h / 2;
  const corners = [{ x: l, y: t }, { x: r, y: t }, { x: r, y: u }, { x: l, y: u }];
  for (let i = 0; i < 4; i++) {
    if (crosses(a, b, corners[i], corners[(i + 1) % 4])) return true;
  }
  return false;
}

/**
 * 並びの読みにくさ。小さいほうを採る。
 *
 * 中心から中心への直線で数える（描画は弧になることがあるが、弧にするのは
 * 同じ段のときだけで、そのときも「間の箱をまたぐほど遠い」ことは変わらない）。
 */
function badness(pos, edges) {
  const segs = [];
  for (const e of edges) {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (a && b) segs.push({ e, a, b });
  }
  let cross = 0;
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const x = segs[i];
      const y = segs[j];
      // 端を共有する 2 本は交わって見えない（同じノードから出ているだけ）
      if (x.e.from === y.e.from || x.e.from === y.e.to
        || x.e.to === y.e.from || x.e.to === y.e.to) continue;
      if (crosses(x.a, x.b, y.a, y.b)) cross++;
    }
  }
  let through = 0;
  let length = 0;
  for (const s of segs) {
    length += Math.hypot(s.b.x - s.a.x, s.b.y - s.a.y);
    for (const [ref, box] of pos) {
      if (ref === s.e.from || ref === s.e.to) continue;
      if (throughBox(s.a, s.b, box)) through++;
    }
  }
  return cross * W_CROSS + through * W_THROUGH + length * W_LENGTH;
}

/**
 * 段の並びから座標を作る。**行は中央で揃える。**
 *
 * 左端で揃えると、語数の少ない段が左に寄ったまま残り、そこへ繋がる線が全部
 * 斜めに走る（孤立した語を外したあとは段ごとの語数の差が大きくなるので、
 * ここを直さないと外した効きが半分になる）。
 */
function placeRows(rows, byRef) {
  const widths = rows.map((row) => row.map((ref) => nodeWidth(byRef.get(ref).term)));
  const spans = widths.map(
    (ws) => ws.reduce((a, b) => a + b, 0) + GAP_X * Math.max(0, ws.length - 1)
  );
  const span = Math.max(0, ...spans);
  const width = span + PAD * 2;
  const pos = new Map();
  rows.forEach((row, r) => {
    let x = PAD + (span - spans[r]) / 2;
    row.forEach((ref, i) => {
      const w = widths[r][i];
      pos.set(ref, { x: x + w / 2, y: PAD + NODE_H / 2 + r * GAP_Y, w, h: NODE_H });
      x += w + GAP_X;
    });
  });
  return { pos, width, height: PAD * 2 + NODE_H + (rows.length - 1) * GAP_Y };
}

/** 隣接ノードの平均位置へ寄せた並び。同点は今の並びを保つ（結果が揺れないため）。 */
function relaxRow(row, pos, neighbors) {
  return row
    .map((ref, i) => {
      const near = (neighbors.get(ref) || [])
        .map((other) => pos.get(other)?.x)
        .filter((x) => x !== undefined);
      const here = pos.get(ref)?.x ?? i;
      const key = near.length ? near.reduce((a, b) => a + b, 0) / near.length : here;
      return { ref, key, here };
    })
    .sort((a, b) => a.key - b.key || a.here - b.here)
    .map((x) => x.ref);
}

/** 層が 1 枚しかないときの円配置。横一列に並べると辺が全部重なる。 */
function circleLayout(nodes, edges) {
  const n = nodes.length;
  // 半径は**箱が円周に並ぶのに要る長さ**から決める。決め打ちの余白を足すと、
  // 数語のときに図の何倍もの空白が残る（`NODE_MAX_W` を当て込んでいた）
  const widths = nodes.map((node) => nodeWidth(node.term));
  const need = widths.reduce((a, w) => a + w + GAP_X, 0) / (2 * Math.PI);
  const radius = Math.max(96, need);
  const cx = radius + Math.max(...widths) / 2 + PAD;
  const cy = radius + NODE_H / 2 + PAD;
  const pos = new Map();
  // 円周上でも、繋がっているものを隣どうしに置く（弦が短いほど読める）
  const rank = seedOrder(nodes, edges);
  const ring = [...nodes].sort((a, b) => (rank.get(a.ref) ?? 0) - (rank.get(b.ref) ?? 0));
  ring.forEach((node, i) => {
    // 上から時計回り。1 つだけのときは中央に置く
    const angle = n === 1 ? 0 : (i / n) * 2 * Math.PI - Math.PI / 2;
    pos.set(node.ref, {
      x: n === 1 ? cx : cx + radius * Math.cos(angle),
      y: n === 1 ? cy : cy + radius * Math.sin(angle),
      w: nodeWidth(node.term),
      h: NODE_H,
    });
  });
  return { pos, width: cx * 2, height: cy * 2 };
}

/**
 * 段の中の並びを決める。
 *
 * 隣接ノードの平均位置へ寄せる緩和を `RELAX_PASSES` 回まわし、**その途中で
 * いちばん読みやすかったものを採る**（`badness`）。回数で必ず止めるので、
 * 上下に矛盾があっても終わらなくならない。乱数も時刻も使わないので、
 * 同じ辞書なら毎回同じ絵になる。
 */
function orderRows(base, byRef, edges, neighbors, rank) {
  const seeds = [
    base.map((row) => [...row]),
    base.map((row) => [...row].sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0))),
  ];
  let best = null;
  for (const seed of seeds) {
    let rows = seed;
    for (let pass = 0; pass <= RELAX_PASSES; pass++) {
      const placed = placeRows(rows, byRef);
      const score = badness(placed.pos, edges);
      if (!best || score < best.score) best = { ...placed, score };
      const next = rows.map((row) => relaxRow(row, placed.pos, neighbors));
      if (next.every((row, i) => row.join(SEP) === rows[i].join(SEP))) break;
      rows = next;
    }
  }
  return best;
}

function layeredLayout(nodes, edges, level) {
  const byRef = new Map(nodes.map((n) => [n.ref, n]));
  const neighbors = new Map(nodes.map((n) => [n.ref, []]));
  for (const e of edges) {
    neighbors.get(e.from)?.push(e.to);
    neighbors.get(e.to)?.push(e.from);
  }
  const buckets = new Map();
  for (const node of nodes) {
    const l = level.get(node.ref) || 0;
    if (!buckets.has(l)) buckets.set(l, []);
    buckets.get(l).push(node.ref);
  }
  const base = [...buckets.keys()].sort((a, b) => a - b).map((l) => buckets.get(l));
  return orderRows(base, byRef, edges, neighbors, seedOrder(nodes, edges));
}

/**
 * 関係の書かれていない語を図の下に帯で並べる。
 *
 * **段の外**なので上下の意味は持たない（`levelsOf` の段と混ぜない）。図の幅で
 * 折り返す —— 一列に並べると、外したはずの語がまた図を横に伸ばす。
 * 区切り線と見出しを描くのは呼ぶ側（`draw`）。
 */
function appendLonely(layout, lonely) {
  if (!lonely.length) return layout;
  const widths = lonely.map((n) => nodeWidth(n.term));
  const limit = Math.max(layout.width, 640) - PAD * 2;
  const lines = [];
  let line = null;
  let used = 0;
  lonely.forEach((node, i) => {
    const w = widths[i];
    if (!line || used + GAP_X + w > limit) {
      line = [];
      lines.push(line);
      used = 0;
    }
    line.push({ node, w });
    used += (line.length > 1 ? GAP_X : 0) + w;
  });

  const spans = lines.map(
    (l) => l.reduce((a, b) => a + b.w, 0) + GAP_X * Math.max(0, l.length - 1)
  );
  const width = Math.max(layout.width, Math.max(...spans) + PAD * 2);
  // 帯のほうが広いときは**段のほうも中央へ寄せる**。片方だけ中央だと、
  // 図が左に寄ったまま下だけ真ん中、という揃っていない絵になる
  const shift = (width - layout.width) / 2;
  const pos = new Map(
    [...layout.pos].map(([ref, p]) => [ref, shift ? { ...p, x: p.x + shift } : p])
  );
  const top = (layout.height ? layout.height - PAD + LONELY_GAP : PAD) + NODE_H / 2;
  lines.forEach((l, r) => {
    let x = (width - spans[r]) / 2;
    for (const { node, w } of l) {
      pos.set(node.ref, {
        x: x + w / 2,
        y: top + r * (NODE_H + LONELY_LINE_GAP),
        w,
        h: NODE_H,
      });
      x += w + GAP_X;
    }
  });
  return {
    pos,
    width,
    height: top + (lines.length - 1) * (NODE_H + LONELY_LINE_GAP) + NODE_H / 2 + PAD,
    // 区切り線と見出しの高さ。図の本体と帯の境目
    lonelyTop: top - NODE_H / 2 - 22,
  };
}

/** 箱の中心から中心へ引いた線が、箱の縁と交わる点。 */
function edgePoint(from, to) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (!dx && !dy) return { x: from.x, y: from.y };
  const hw = from.w / 2 + 4;
  const hh = from.h / 2 + 4;
  const scale = Math.min(
    dx ? hw / Math.abs(dx) : Infinity,
    dy ? hh / Math.abs(dy) : Infinity
  );
  return { x: from.x + dx * scale, y: from.y + dy * scale };
}

// --------------------------------------------------------------------------- //
// 拡大縮小と移動
//
// **動かすのは `viewBox`。** CSS の `transform` で拡大すると、線もラベルも画像を
// 引き伸ばしたようににじむ（SVG の意味が無い）。`viewBox` なら文字は文字のまま
// 描き直される。入れ物のほうは大きさを固定して、はみ出したぶんはスクロールでは
// なく**動かして見る** —— 巨大な図をスクロールバーで追うのは、どこを見ているのか
// 分からなくなる（元は入れ物ごと横に伸びていた）。
// --------------------------------------------------------------------------- //

//: 拡大率の上下限。下は「全体を出す」で足りるので、それより縮める必要は薄い
const MIN_SCALE = 0.15;
const MAX_SCALE = 4;

/** いま描いてある svg と、その中身が収まる枠。「全体を出す」の基準 */
let svgRoot = null;
let contentBox = null;
/** いま出している範囲（利用者座標）。null なら図が無い */
let view = null;
/** 入れ物の大きさが変わったときに追随するための番人。**開き直すたびに外す** */
let viewWatch = null;
/** 直前に見えていた入れ物の大きさ。変化のぶんだけ範囲を広げ縮めするのに使う */
let viewport = { w: 0, h: 0 };
/** 掴んで動かした直後のクリックを飲む（線を押して編集ダイアログが開くのを防ぐ） */
let swallowClick = false;

const viewScale = () => (view && canvas?.clientWidth ? canvas.clientWidth / view.w : 1);

function applyView() {
  if (!svgRoot || !view) return;
  svgRoot.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
}

/**
 * 全体が入るところまで戻す。
 *
 * **拡大はしない**（`Math.min(…, 1)`）。1 語だけの図を入れ物いっぱいに引き伸ばすと、
 * 箱だけが画面を覆って何も分からない絵になる。
 */
function fitView() {
  if (!contentBox || !canvas) return;
  const vw = canvas.clientWidth || contentBox.w;
  const vh = canvas.clientHeight || contentBox.h;
  const scale = Math.min(vw / contentBox.w, vh / contentBox.h, 1);
  const w = vw / scale;
  const h = vh / scale;
  view = {
    x: contentBox.x + contentBox.w / 2 - w / 2,
    y: contentBox.y + contentBox.h / 2 - h / 2,
    w,
    h,
  };
  viewport = { w: canvas.clientWidth, h: canvas.clientHeight };
  applyView();
}

/** ``client`` 座標の点を動かさずに拡大率を ``factor`` 倍する。 */
function zoomAt(clientX, clientY, factor) {
  if (!view || !canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const scale = rect.width / view.w;
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
  if (next === scale) return;
  // 押さえた点の利用者座標。ここが動かないように左上を決め直す
  const fx = (clientX - rect.left) / rect.width;
  const fy = (clientY - rect.top) / rect.height;
  const ux = view.x + fx * view.w;
  const uy = view.y + fy * view.h;
  view.w = rect.width / next;
  view.h = rect.height / next;
  view.x = ux - fx * view.w;
  view.y = uy - fy * view.h;
  applyView();
}

/** 入れ物の中心を軸に拡大縮小する（ボタンとキーボード用）。 */
function zoomBy(factor) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
}

/** 画面上の px で動かす。 */
function panBy(dx, dy) {
  if (!view || !canvas?.clientWidth) return;
  const scale = canvas.clientWidth / view.w;
  view.x += dx / scale;
  view.y += dy / scale;
  applyView();
}

/**
 * 見えていないものへ焦点が移ったら、そこまで動かす。
 *
 * **スクロールをやめたぶん、これが要る。** 入れ物が勝手に送ってくれなくなったので、
 * Tab でノードや線をたどると**焦点だけが枠の外へ出て見失う**（キーボードで
 * 図を歩けなくなる）。
 */
function bringIntoView(node) {
  if (!view || !node) return;
  let box;
  try {
    box = node.getBBox();
  } catch {
    return;                          // まだ描かれていない要素
  }
  const m = 24;
  const before = { x: view.x, y: view.y };
  if (box.x - m < view.x) view.x = box.x - m;
  else if (box.x + box.width + m > view.x + view.w) {
    view.x = box.x + box.width + m - view.w;
  }
  if (box.y - m < view.y) view.y = box.y - m;
  else if (box.y + box.height + m > view.y + view.h) {
    view.y = box.y + box.height + m - view.h;
  }
  if (view.x !== before.x || view.y !== before.y) applyView();
}

/**
 * 入れ物と図に操作を仕掛ける。**`mount()` から 1 回だけ呼ぶ。**
 *
 * listener は入れ物そのものに付ける（中身は描き直しで入れ替わる）。
 * `ResizeObserver` だけは DOM を捨てても残るので、開き直すたびに外している
 * —— 覆いは何度でも開かれるので、ここを怠ると開いた回数だけ増える。
 */
function installViewControls() {
  viewWatch?.disconnect();
  viewWatch = null;

  // ホイールは拡大縮小に使う。図は入れ物の中で完結していて、ここで送るものが
  // 他に無い（ページ側は `preventDefault` で止める）
  canvas.addEventListener("wheel", (ev) => {
    if (!view) return;
    ev.preventDefault();
    const step = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY;
    zoomAt(ev.clientX, ev.clientY, Math.exp(-step * 0.0016));
  }, { passive: false });

  let drag = null;
  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || !view) return;
    swallowClick = false;
    drag = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, moved: false };
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag || ev.pointerId !== drag.id) return;
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    // **少し動いたくらいでは掴んだことにしない。** 押しただけで動いた扱いにすると、
    // 線やノードを押したつもりが「動かした」になってダイアログが開かない
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    if (!drag.moved) {
      // **掴みは動き出してから捕まえる。** `pointerdown` で捕まえると、以後の
      // ポインタ事象（互換の mouseup を含む）が入れ物へ付け替えられ、
      // **中のノードや線を押しても click がそこへ届かない**（線を押しても
      // 編集ダイアログが開かなくなった）
      canvas.setPointerCapture(ev.pointerId);
      canvas.classList.add("grabbing");
    }
    drag.moved = true;
    swallowClick = true;
    drag.x = ev.clientX;
    drag.y = ev.clientY;
    panBy(-dx, -dy);
  });
  const endDrag = (ev) => {
    if (!drag || ev.pointerId !== drag.id) return;
    if (drag.moved) canvas.releasePointerCapture?.(drag.id);
    drag = null;
    canvas.classList.remove("grabbing");
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  // 掴んで動かしたあとのクリックは飲む。**必ず戻す**ので、動かさなかった次の
  // クリックまで飲み続けることはない
  canvas.addEventListener("click", (ev) => {
    if (!swallowClick) return;
    swallowClick = false;
    ev.preventDefault();
    ev.stopPropagation();
  }, true);
  // ノードは <a> なので、掴むと既定ではリンクを引きずってしまう
  canvas.addEventListener("dragstart", (ev) => ev.preventDefault());

  canvas.addEventListener("keydown", (ev) => {
    if (!view || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const step = ev.shiftKey ? 120 : 40;
    const moves = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0],
      ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    if (moves[ev.key]) {
      ev.preventDefault();
      panBy(...moves[ev.key]);
    } else if (ev.key === "+" || ev.key === "=") {
      ev.preventDefault();
      zoomBy(1.25);
    } else if (ev.key === "-") {
      ev.preventDefault();
      zoomBy(1 / 1.25);
    } else if (ev.key === "0") {
      ev.preventDefault();
      fitView();
    }
  });
  canvas.addEventListener("focusin", (ev) => {
    const node = ev.target.closest?.(".rel-node, .rel-edge-group");
    if (node) bringIntoView(node);
  });

  zoomBar.querySelector("#zoomIn").addEventListener("click", () => zoomBy(1.25));
  zoomBar.querySelector("#zoomOut").addEventListener("click", () => zoomBy(1 / 1.25));
  zoomBar.querySelector("#zoomFit").addEventListener("click", fitView);

  if (typeof ResizeObserver === "undefined") return;
  viewWatch = new ResizeObserver(() => {
    const vw = canvas.clientWidth;
    const vh = canvas.clientHeight;
    if (!view || !vw || !vh) return;
    // **入れ物の大きさが分からないうちに描いていたら**、ここで初めて全体に合わせる
    // （覆いの中など、描く時点で 0 のことがある）
    if (!viewport.w) {
      fitView();
      return;
    }
    // 幅が変わっただけで**見え方までは変えない**（拡大率と中心を保つ）
    const scale = viewport.w / view.w;
    const cx = view.x + view.w / 2;
    const cy = view.y + view.h / 2;
    view.w = vw / scale;
    view.h = vh / scale;
    view.x = cx - view.w / 2;
    view.y = cy - view.h / 2;
    viewport = { w: vw, h: vh };
    applyView();
  });
  viewWatch.observe(canvas);
}

// --------------------------------------------------------------------------- //
// 描画
// --------------------------------------------------------------------------- //

function marker(id, className) {
  return svg("marker", {
    id,
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 7,
    markerHeight: 7,
    orient: "auto-start-reverse",
  }, [svg("path", { d: "M0,0 L10,5 L0,10 z", class: className })]);
}

//: ラベルを線上のどこに置くか。**線の上ならどこでもよい**ので、先に置いたものと
//: 重ならない候補を順に試す。中点に固定すると、1 つのノードに何本も集まったとき
//: 文字が重なって読めない（通し番号でずらすだけでは足りなかった）
const LABEL_TS = [0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78];
//: **弧が膨らんだ側から先に試す。** 上下どちらも同じ順で試すと、下へ逃がした弧の
//: ラベルだけが段をまたぐ線の帯（＝逃がしたかった側）へ戻ってしまう
const LABEL_DYS_UP = [-8, -22, 6, -36, 20, -50, 34, -64, 48];
const LABEL_DYS_DOWN = [10, 24, -8, 38, -22, 52, 34, 66, -36];

/** 同じ段のノードを結ぶ弧の高さ。**描画と余白の計算で同じ値を使う。** */
function sameRowLift(from, to, parallel) {
  return Math.min(90, 24 + Math.abs(to.x - from.x) * 0.18) + parallel * 22;
}

/**
 * 同じ段を結ぶ弧を、段の上下どちらへ出すか決める（辺の番号 → +1 下 / -1 上）。
 *
 * **片側へ寄せない。** 「対等」の関係が多いと一群がまるごと 1 つの段に集まり、
 * その段の中の関係が全部その段の上へ弧で載る（実例では 11 語の段に 15 本）。
 * そこは段をまたぐ線も通る帯なので、線もラベルも折り重なって読めなくなる ——
 * 一方で反対側はほとんど空いている。
 *
 * いちばん外側の段だけは外へ逃がす（そちらは何も通らない）。挟まれた段は
 * **長い弧から順に交互**に振り分ける —— 大きく張り出すものを同じ側に集めない。
 */
function arcSides(edges, pos) {
  const ys = [...new Set([...pos.values()].map((p) => p.y))].sort((a, b) => a - b);
  const top = ys[0];
  const floor = ys[ys.length - 1];
  const sides = new Map();
  const middle = new Map();                 // 段の y -> [辺の番号]
  edges.forEach((edge, i) => {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    if (!a || !b || Math.abs(a.y - b.y) >= 1) return;
    if (Math.abs(a.y - floor) < 1) sides.set(i, 1);          // 下は空いている
    else if (Math.abs(a.y - top) < 1) sides.set(i, -1);      // 上は空いている
    else {
      if (!middle.has(a.y)) middle.set(a.y, []);
      middle.get(a.y).push(i);
    }
  });
  for (const group of middle.values()) {
    const span = (i) => Math.abs(pos.get(edges[i].to).x - pos.get(edges[i].from).x);
    // 同点は辺の番号順（並びが揺れないように）
    group.sort((a, b) => span(b) - span(a) || a - b);
    group.forEach((i, n) => sides.set(i, n % 2 ? -1 : 1));
  }
  return sides;
}

/**
 * 辺の形。**線・ラベル・枠の計算はすべてここから採る** —— 別々に組み立てると、
 * ラベルだけ線から外れたり、枠が弧を切ったりする（実際にどちらも踏んだ）。
 *
 * @param {number} parallel 同じ 2 ノードを結ぶ何本目か。0 なら 1 本目。
 *   同じ組を複数の関係が結ぶことがある（「親友」と「実は〜」など）。ずらさないと
 *   線もラベルも完全に重なって、2 本あることすら分からなくなる
 * @param {number} side 同じ段を結ぶ弧をどちら側へ出すか（+1 で下、-1 で上）。
 *   決めるのは `arcSides()`。全部を上へ出すと、段をまたぐ線が通っているまさに
 *   その帯へ重なる（「対等」の多い一群が 1 つの段に集まるので、実例ではそこに
 *   15 本ぶんの線とラベルが折り重なり、段の下は空いたままだった）
 */
function edgeGeometry(edge, pos, parallel = 0, side = -1) {
  const a = pos.get(edge.from);
  const b = pos.get(edge.to);
  if (!a || !b) return null;
  const start = edgePoint(a, b);
  const end = edgePoint(b, a);

  // 同じ層のノード同士は、間にある箱を避けて弧で結ぶ
  let ctrl = null;
  let down = false;
  if (Math.abs(a.y - b.y) < 1) {
    const lift = sameRowLift(start, end, parallel);
    down = side > 0;
    ctrl = { x: (start.x + end.x) / 2, y: start.y + (down ? lift : -lift) };
  } else if (parallel) {
    // 段をまたぐ 2 本目以降は、線に直交する向きへ膨らませる
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const len = Math.hypot(dx, dy) || 1;
    const off = parallel * 26;
    ctrl = {
      x: (start.x + end.x) / 2 - (dy / len) * off,
      y: (start.y + end.y) / 2 + (dx / len) * off,
    };
  }
  const at = (t) => (ctrl
    ? {
        x: (1 - t) ** 2 * start.x + 2 * (1 - t) * t * ctrl.x + t * t * end.x,
        y: (1 - t) ** 2 * start.y + 2 * (1 - t) * t * ctrl.y + t * t * end.y,
      }
    : { x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t });
  return {
    edge,
    start,
    end,
    ctrl,
    down,
    at,
    d: ctrl
      ? `M ${start.x} ${start.y} Q ${ctrl.x} ${ctrl.y} ${end.x} ${end.y}`
      : `M ${start.x} ${start.y} L ${end.x} ${end.y}`,
    words: relationWords(edge),
    length: Math.hypot(end.x - start.x, end.y - start.y),
  };
}

//: 一言は 11px。見積もりの規則は base.js（fabric.js と同じ）
const textWidth = (text) => estTextWidth(text, 11);

/**
 * ラベルが占める箱（`x` `y` は中央寄せしたときの基準点）。
 *
 * **実測より少し大きく取る。** 見積もりが実物より小さいと、置いたあとで
 * 重なる（1.5px 足りないだけで、14px 差で並んだ 2 行が実際には触れていた）。
 * 幅は全角 11.5 / 半角 6.2 で、実測より 1〜2 割広めに出る。
 */
function labelBox(text, x, y) {
  const w = textWidth(text);
  return { x: x - w / 2 - 2, y: y - 12, w: w + 4, h: 16 };
}

const boxesOverlap = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * ラベルを置く場所を決める。
 *
 * ノードの箱と、先に置いたラベルを避ける。**短い線から先に選ばせる** ——
 * 動かせる幅が狭いのはそちらで、長い線はどこへでも逃がせる。
 * どこも空いていなければ、いちばん重なりの少ないところに置く（消しはしない。
 * 黙って欠けたラベルは「関係に一言が書かれていない」と見分けが付かない）。
 */
function placeLabels(geoms, pos) {
  const taken = [...pos.values()].map((p) => ({
    x: p.x - p.w / 2, y: p.y - p.h / 2, w: p.w, h: p.h,
  }));
  const spots = new Map();
  const order = geoms
    .map((g, i) => ({ g, i }))
    .filter((x) => x.g && x.g.words)
    .sort((a, b) => a.g.length - b.g.length || a.i - b.i);
  for (const { g, i } of order) {
    let spot = null;
    for (const dy of g.down ? LABEL_DYS_DOWN : LABEL_DYS_UP) {
      for (const t of LABEL_TS) {
        const p = g.at(t);
        const box = labelBox(g.words, p.x, p.y + dy);
        let clash = 0;
        for (const b of taken) if (boxesOverlap(box, b)) clash++;
        if (!spot || clash < spot.clash) spot = { x: p.x, y: p.y + dy, box, clash };
        if (!clash) break;
      }
      if (spot && !spot.clash) break;
    }
    // **どこも空いていなければ畳む。** 重ねて出すと、重なった 2 つとも読めなく
    // なるうえ、下にある線まで隠す（実例では 28 本ぶんの一言が帯状に潰れていた）。
    // 消しはしない —— 数を画面に出し、線かその語に乗せれば出る。
    // **畳んだものは場所を取らせない**（後続の一言に空きを残す）
    spot.tucked = spot.clash > 0;
    if (!spot.tucked) taken.push(spot.box);
    spots.set(i, spot);
  }
  return spots;
}

/**
 * 押せる帯の大きさを、部品の外形として持たせるための（塗らない）四角。
 *
 * **外形に線の太さは入らない。** 真横・真縦の辺はそれだけで幅か高さが 0 になり、
 * 押せるのに「大きさの無い部品」として扱われる（焦点の枠も、外形で見る道具も
 * 何も見つけられない）。押せる範囲そのものを外形にしておく。
 */
function hitBand(geom) {
  const pts = [];
  for (let i = 0; i <= 16; i++) pts.push(geom.at(i / 16));
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const pad = 7;                      // `.rel-edge-hit` の太さの半分
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return svg("rect", {
    class: "rel-edge-band",
    x: x - pad,
    y: y - pad,
    width: Math.max(...xs) - x + pad * 2,
    height: Math.max(...ys) - y + pad * 2,
  });
}

/**
 * 1 本の辺。線と一言を**別々に返す** —— 一言は線より上の層へまとめて置く。
 *
 * 同じ層に混ぜると、**あとに描いた辺の線が前の辺の一言を横切る**（下になった
 * 文字は縁取りごと消えて読めない）。線は線どうし、文字は文字どうしで重ねる。
 */
function drawEdge(geom, spot) {
  const { edge, d, words } = geom;
  const cls = ["rel-edge", edge.missing ? "missing" : "", edge.reveal ? "reveal" : ""]
    .filter(Boolean)
    .join(" ");
  const path = svg("path", {
    d,
    class: cls,
    "marker-end": "url(#arrow)",
    // 相互なら両端に矢印。一方的なら向いている側だけ
    "marker-start": edge.mutual ? "url(#arrow)" : null,
  });
  const detail = describeRelation(edge, {
    from: termByRef.get(edge.from), to: termByRef.get(edge.to),
  });
  path.append(svg("title", { text: `${detail}（押すと直せます）` }));

  // **線そのものは細すぎて押せない。** 透明な太い線を下に重ねて当たり判定にする
  const group = svg("g", {
    class: "rel-edge-group",
    tabindex: "0",
    role: "button",
    "aria-label": `関係を直す: ${detail}`,
    // 図の下の枠に出す文。**一言を切っている見せ方でも全文がここで読める**
    "data-detail": detail,
  }, [hitBand(geom), svg("path", { d, class: "rel-edge-hit" }), path]);

  const text = words && spot
    ? svg("text", {
        x: spot.x,
        y: spot.y,
        // 置き場所が無かったものは畳んでおく（線かその語に乗せると出る）
        class: spot.tucked ? "rel-edge-label tucked" : "rel-edge-label",
        "text-anchor": "middle",
        "data-detail": detail,
        text: words,
      })
    : null;
  // **一言に `<title>` は入れない。** `<text>` の中に置くと、描かれないまま
  // 文字の内容として数えられ、「一言」を読む側（テストも含む）が別物を読む

  // **線と一言は一緒に光らせる。** 線だけ色が変わっても、どの一言の関係なのかが
  // 分からない（一言は空いているところへ逃がすので、線の真上とは限らない）。
  // 層が分かれていて CSS の子孫セレクタが届かないので、両方に印を付けて回る
  const light = (on) => {
    group.classList.toggle("hot", on);
    text?.classList.toggle("hot", on);
  };
  const open = () => openEdgeEditor(edge);
  for (const el of [group, text]) {
    if (!el) continue;
    el.addEventListener("pointerenter", () => light(true));
    el.addEventListener("pointerleave", () => light(false));
    el.addEventListener("click", open);
  }
  group.addEventListener("focus", () => light(true));
  group.addEventListener("blur", () => light(false));
  group.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    open();
  });
  return { group, text };
}

function edgeTitle(edge) {
  const bits = [edge.mutual ? "相互" : "一方的"];
  if (edge.label) bits.push(edge.label);
  if (edge.rank) bits.push(`相手が${edge.rank}`);
  if (edge.reveal) bits.push(`判明: ${edge.reveal}`);
  return bits.join(" / ");
}

function drawNode(node, pos) {
  const p = pos.get(node.ref);
  if (!p) return null;
  const cls = ["rel-node", node.missing ? "missing" : "", node.outside ? "outside" : ""]
    .filter(Boolean)
    .join(" ");
  const group = svg("g", { class: cls, "data-detail": describeNode(node) });
  const link = svg("a", { href: node.url });
  link.append(
    svg("rect", {
      x: p.x - p.w / 2,
      y: p.y - p.h / 2,
      width: p.w,
      height: p.h,
      rx: 10,
    }),
    svg("text", {
      x: p.x,
      y: p.y,
      "text-anchor": "middle",
      "dominant-baseline": "central",
      text: node.term,
    })
  );
  link.append(
    svg("title", {
      text: node.missing
        ? `${node.term} — 未登録（押すと辞書で探せます）`
        : [node.path_label, node.summary].filter(Boolean).join(" — "),
    })
  );
  group.append(link);
  return group;
}

/**
 * 1 つの語に乗せている間、**その語の関係だけを濃く出す**。
 *
 * 交差はどう並べても消せない（→ docs/design-notes.md）。密なところは一度に
 * 全部を読ませようとせず、見たい 1 語のまわりだけ残すのが唯一きく手。
 * 畳んだ一言もここで出す —— 「この語が誰とどうなのか」がまとめて読める。
 *
 * listener は入れ物ではなくノードに付ける（描き直しで一緒に捨てられる）。
 */
function installFocus(root, nodeGroups, touching) {
  const light = (ref, on) => {
    root.classList.toggle("focusing", on);
    if (!on) {
      for (const el of root.querySelectorAll(".lit")) el.classList.remove("lit");
      return;
    }
    nodeGroups.get(ref)?.classList.add("lit");
    for (const { group, text, other } of touching.get(ref) || []) {
      group.classList.add("lit");
      text?.classList.add("lit");
      nodeGroups.get(other)?.classList.add("lit");
    }
  };
  for (const [ref, group] of nodeGroups) {
    group.addEventListener("pointerenter", () => light(ref, true));
    group.addEventListener("pointerleave", () => light(ref, false));
    // キーボードでも同じ（ノードの中の <a> に焦点が入る）
    group.addEventListener("focusin", () => light(ref, true));
    group.addEventListener("focusout", () => light(ref, false));
  }
}

/**
 * 関係の無い語をまとめた帯の区切り（線と見出し）。**段ではない**と分かるように。
 *
 * 上に段があるときだけ描く。全部が孤立しているときは区切るものが無く、
 * 「下は段の外」という説明が指すものも無い。
 */
function lonelyRule(top, width, count) {
  const caption = `関係が書かれていない語（${count}）`;
  return {
    node: svg("g", { class: "rel-lonely-rule" }, [
      svg("line", { x1: PAD, y1: top, x2: Math.max(width - PAD, PAD + 40), y2: top }),
      svg("text", { x: PAD, y: top - 6, class: "rel-lonely-caption", text: caption }),
    ]),
    // 見出しは左端から伸びるので、幅の計算に入れないと右で切れる（実際に切れた）
    box: { x: PAD, y: top - 16, w: textWidth(caption), h: 13 },
  };
}

/** 描いたものが収まる枠。**中身から決める**（段の高さだけで作ると弧とラベルが切れる） */
function boundsOf(pos, geoms, spots, width, height, extras = []) {
  let minX = 0;
  let minY = 0;
  let maxX = width;
  let maxY = height;
  const grow = (x, y) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  for (const p of pos.values()) {
    grow(p.x - p.w / 2, p.y - p.h / 2);
    grow(p.x + p.w / 2, p.y + p.h / 2);
  }
  geoms.forEach((g, i) => {
    if (!g) return;
    // 制御点は曲線より外側にある。少し広く取るぶんには害が無い
    if (g.ctrl) grow(g.ctrl.x, g.ctrl.y);
    const spot = spots.get(i);
    if (!spot) return;
    grow(spot.box.x, spot.box.y);
    grow(spot.box.x + spot.box.w, spot.box.y + spot.box.h);
  });
  for (const box of extras) {
    grow(box.x, box.y);
    grow(box.x + box.w, box.y + box.h);
  }
  return { minX: minX - 8, minY: minY - 8, maxX: maxX + 8, maxY: maxY + 8 };
}

/** 描く。戻り値は画面に添える数（帯にまわした語の数）。 */
/**
 * 描く。戻り値は画面に添える数（帯にまわした語の数と、畳んだ一言の数）。
 *
 * **見せ方の分岐はここ 1 か所。** どちらの見せ方も `{ root, box }` を返し、
 * 入れ物への差し込み・拡大縮小の初期化・凡例は共通で面倒を見る
 * （見せ方ごとに別々の道を作ると、片方だけ拡大できない、が起きる）。
 */
function draw(graph) {
  const { nodes, edges } = graph;
  termByRef = new Map(nodes.map((n) => [n.ref, n.term]));
  svgRoot = null;
  contentBox = null;
  view = null;
  viewport = { w: 0, h: 0 };
  zoomBar.hidden = true;
  canvas.classList.add("is-empty");
  if (!nodes.length) {
    canvas.replaceChildren(
      el("p", { class: "empty", text: "このカテゴリには関係が書かれたエントリがありません。" })
    );
    return { lonely: 0 };
  }

  // 地図は**座標を書いた語がある辞書でしか出せない**。時系列と同じ扱いで、
  // 選べないときも**黙って別の図に差し替えない**（注意書きを出し、覚えている
  // 選択のほうは書き換えない）
  mapFellBack = mode === "map" && !(graph.maps || []).length;
  // 時系列は**軸が 2 つ**。どちらも使えなければ出せない（地図と同じ扱いで、
  // 黙って別の図に差し替えず、注意書きを出す）
  drawnAxis = mode === "timeline" ? axisFor(graph) : "";
  axisFellBack = drawnAxis && drawnAxis !== timeAxis ? drawnAxis : "";
  modeFellBack = mode === "timeline" && !drawnAxis;
  const build = {
    fabric: buildFabric, matrix: buildMatrix, ego: buildEgo,
    timeline: modeFellBack ? null : buildTimeline,
    map: mapFellBack ? null : buildMap,
  }[mode];
  const drawn = build
    ? build(graph, {
      onEdge: openEdgeEditor,
      center: egoCenter,
      onCenter: moveCenter,
      depth: egoDepth,
      axis: drawnAxis,
      rows: timeRows,
      mapName,
      hidden: mapHidden.get(mapName),
      editing: mapEditing,
      // **種別は押す前に宣言する**（点の数から機械が決めない）ので、
      // 何として置くのかも一緒に渡す
      placing: mapPlacing,
      onMove: saveMapShape,
      onPlace: (ref, kind, points) => {
        mapPlacing = null;
        saveMapShape(ref, kind, points);
      },
      // 押すたびに何点目かを出す。**図は描き直さない**（描き直すと置きかけが
      // 消える）ので、live に出せる場所はここしかない
      onPlaceProgress: (ref, kind, count) => setStatus(
        statusNode,
        `${KIND_WORDS[kind]}を作っています: ${count} 点`
        + `（Enter か「✓ 確定」で決定 / Esc でやめる）`
      ),
      onPlaceCancel: () => {
        mapPlacing = null;
        if (lastGraph) paintGraph(lastGraph);
        setStatus(statusNode, "置くのをやめました");
      },
      // 断りは**その場に出す**（形が変わらないので、出さないと何も起きて
      // いないように見える）
      onRefuse: (message) => setStatus(statusNode, message, "error"),
      labels: !mapNoLabels.has(mapName),
      // **出すほうを覚える**（名前とは逆）。既定は出さない —— 点が絵になると
      // 図の見え方が大きく変わるので、頼まれていないのに変えない
      images: mapFaces.has(mapName),
      // 時点（`null` は全部）。**覚えない** —— 伏せたまま開き直すと
      // 「関係が消えた」と読まれる（「ここまで読んだぶん」と同じ約束）
      upTo: mapAt,
      // 絵の縦横比は読み込むまで分からない。届いたら入れ物に合わせ直す
      onResize: () => fitView(),
    })
    : buildLayered(nodes, edges);
  // 中心は図が決めることもある（指した語が範囲の外なら、いちばん多く繋がっている
  // 語に落ちる）。押した先が同じ語のままにならないよう、決まった値を控えておく
  if (drawn.center) egoCenter = drawn.center;
  // 出す絵は図が決めることもある（覚えている絵がこの範囲に無ければ、いちばん
  // 多く点が乗るものに落ちる）。押した先が同じにならないよう控えておく
  if (drawn.map) mapName = drawn.map;
  canvas.classList.remove("is-empty");
  canvas.replaceChildren(drawn.root);
  svgRoot = drawn.root;
  contentBox = drawn.box;
  zoomBar.hidden = false;
  fitView();
  // **名指しされた語に目印を付ける。** 地図だけでなく年表でも要る —— 用語ページの
  // 「辞書の図で見る →」から渡ってくるので、**どこに自分の語があるか**が分からないと
  // 渡った意味が無い（広い絵の隅、長い年表の途中、どちらも同じ困り方）。
  // 段の図など置き場所を計算する図では、**乗せたときと同じ道具**が別に効くので足さない
  if (mode === "map") spotlight(namedRef);
  else if (mode === "timeline") spotlight(namedRef, { dim: false });
  return {
    lonely: drawn.lonely,
    linked: nodes.length - drawn.lonely,
    tucked: drawn.tucked || 0,
    // 見せ方が「出していないもの」を数えていれば、そのまま凡例へ通す
    // （**ここで落とすと黙って欠けた図になる**。実際に一度落とした）
    note: drawn.note || "",
    // 中心の図が実際に出した深さ（読めない値は既定に落ちているので、凡例はこれを見る）
    depth: drawn.depth || 0,
    // 地図が「この絵に置ける語」を返していれば、そのままチェックの一覧へ通す
    // （**ここで落とすと一覧が空になり、外したものを戻せなくなる**。実際に落とした）
    items: drawn.items || [],
    pending: drawn.pending || [],
    // 止まれる時点（**ここで落とすとスライダが出ない**。`items` を落として
    // 一覧が空になったのと同じ間違い）
    times: drawn.times || [],
    // 置いている最中なら確定 / やめるの口（**ここで落とすと、押しかけの形を
    // 決める手段が Enter だけになる**。`items` を落として一覧が空になったのと
    // 同じ間違い）
    place: drawn.place || null,
  };
}

/** 段の図（既定）。上下を段で表し、関係は段のあいだ・段の上下に線で結ぶ。 */
function buildLayered(nodes, edges) {
  // 同じ 2 ノードを結ぶ辺が何本目か。弧の高さはこれで決まる
  const pairs = new Map();
  const parallels = edges.map((edge) => {
    const key = [edge.from, edge.to].sort().join(SEP);
    const n = pairs.get(key) || 0;
    pairs.set(key, n + 1);
    return n;
  });

  // 関係の無い語は段に混ぜない（混ぜると繋がっている語どうしを押し広げるだけ）
  const { linked, lonely } = splitLonely(nodes, edges);
  let layout = { pos: new Map(), width: 0, height: 0 };
  let sides = new Map();
  if (linked.length) {
    const level = levelsOf(linked, edges);
    layout = Math.max(0, ...level.values()) > 0
      ? layeredLayout(linked, edges, level)
      : circleLayout(linked, edges);
    sides = arcSides(edges, layout.pos);
    // 最下段の弧は下へ出る。**帯を足す前に**そのぶんの高さを確保する
    // （あとから足すと、区切り線の下に置いた語の上に弧が乗る）
    const floorY = Math.max(...[...layout.pos.values()].map((p) => p.y));
    let room = 0;
    edges.forEach((edge, i) => {
      const a = layout.pos.get(edge.from);
      const b = layout.pos.get(edge.to);
      if (!a || !b || sides.get(i) !== 1 || Math.abs(a.y - floorY) >= 1) return;
      room = Math.max(room, sameRowLift(a, b, parallels[i]) / 2 - NODE_H / 2 + 26);
    });
    if (room > 0) layout = { ...layout, height: layout.height + room };
  }
  layout = appendLonely(layout, lonely);
  const { pos, width, height, lonelyTop } = layout;

  const geoms = edges.map(
    (edge, i) => edgeGeometry(edge, pos, parallels[i], sides.get(i) ?? -1)
  );
  const spots = placeLabels(geoms, pos);
  const rule = lonely.length && linked.length
    ? lonelyRule(lonelyTop, width, lonely.length)
    : null;
  const { minX, minY, maxX, maxY } =
    boundsOf(pos, geoms, spots, width, height, rule ? [rule.box] : []);

  // **大きさは入れ物に合わせ、どこを出すかは `viewBox` で決める**（→ `fitView`）。
  // 中身の寸法を width / height に焼くと、図が伸びたぶん入れ物ごと横に広がる
  const root = svg("svg", {
    class: "rel-graph",
    width: "100%",
    height: "100%",
    viewBox: `${minX} ${minY} ${Math.ceil(maxX - minX)} ${Math.ceil(maxY - minY)}`,
    role: "img",
    "aria-label": "用語の相関図",
  });
  root.append(svg("defs", {}, [marker("arrow", "rel-arrowhead")]));
  if (rule) root.append(rule.node);
  // **層は 3 枚。下から 線 → 一言 → ノード。** 辺ごとに線と一言をまとめて置くと、
  // あとの辺の線が前の辺の一言を横切って読めなくする（実際にそうなった）。
  // ノードをいちばん上にするのは、逃がしきれなかった一言が箱の名前を覆わないため
  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  const touching = new Map(nodes.map((n) => [n.ref, []]));
  let tucked = 0;
  geoms.forEach((g, i) => {
    if (!g) return;
    const spot = spots.get(i);
    const { group, text } = drawEdge(g, spot);
    lines.append(group);
    if (text) labels.append(text);
    if (spot?.tucked && g.words) tucked++;
    touching.get(g.edge.from)?.push({ group, text, other: g.edge.to });
    touching.get(g.edge.to)?.push({ group, text, other: g.edge.from });
  });
  root.append(lines, labels);
  const nodeGroups = new Map();
  for (const node of nodes) {
    const group = drawNode(node, pos);
    if (!group) continue;
    root.append(group);
    nodeGroups.set(node.ref, group);
  }
  installFocus(root, nodeGroups, touching);
  return {
    root,
    box: { x: minX, y: minY, w: maxX - minX, h: maxY - minY },
    lonely: lonely.length,
    tucked,
  };
}

// --------------------------------------------------------------------------- //
// 辺を押して直す
//
// 点検ページを「その場で直す」にしたのと同じ理由。関係を 1 本直すために
// 辞書ページまで移動させると、図を見ながらの手直しが続かない。
// --------------------------------------------------------------------------- //

let edgeDialog;
const dlg = (name) => edgeDialog.querySelector(`[data-ref=${name}]`);

/** ref -> 用語名。ダイアログの見出しに使う（辺が持つのは ref だけ） */
let termByRef = new Map();
/** いま開いている辺 */
let editing = null;

function openEdgeEditor(edge) {
  editing = edge;
  const from = termByRef.get(edge.from) || edge.from;
  const to = termByRef.get(edge.to) || edge.to;
  dlg("who").textContent = `${from} → ${to}`;
  dlg("to").value = edge.rel_to || "";
  dlg("label").value = edge.label || "";
  dlg("back").value = edge.back || "";
  dlg("rank").value = edge.rank || "";
  dlg("reveal").value = edge.reveal || "";
  // **継いだ時刻を欄に入れないこと。** 入れると、この関係を一言だけ直したつもりで
  // 保存した瞬間に**語の時刻が関係へ焼き付く** —— 事件の日付を関係の本数だけ
  // 書き写していた元の形に戻り、しかも次に事件の日付を直しても付いてこない。
  // 継いだぶんは**入力の手引き**として枠の中に薄く出すだけにする
  dlg("when").value = edge.when_inherited ? "" : (edge.when || "");
  dlg("when").placeholder = edge.when_inherited
    ? `空なら「${edge.when}」（語に書いた時刻）で並びます`
    : "例: 1560-05-19 永禄三年五月十九日 / 16世紀 / 約1560（空でよい）";
  setStatus(dlg("status"), "");
  edgeDialog.showModal();
}

/**
 * 書き手のエントリの関係を作り直して保存する。``next`` が null なら削除。
 *
 * **エントリ単位でまとめて書く。** 1 本ずつ書くと、同じエントリに複数の関係が
 * 付いたときに後の書き込みが前のものを消す。
 *
 * 番号は**図を描いた時点**のものなので、書く直前に読み直して行き先が一致するか
 * 確かめる。ずれていたら書かずに読み込み直させる —— 黙って別の関係を書き換えない。
 */
async function writeRelation(next) {
  const edge = editing;
  setStatus(dlg("status"), "保存中", "busy");
  try {
    const entry = await api(`/api/entries/${encodePath(edge.from)}`);
    const relations = [...(entry.relations || [])];
    if (relations[edge.index]?.to !== edge.rel_to) {
      setStatus(dlg("status"), "図が古くなっています。読み込み直してください。", "error");
      return false;
    }
    if (next) relations[edge.index] = next;
    else relations.splice(edge.index, 1);
    await api(`/api/entries/${encodePath(edge.from)}`, {
      method: "PUT",
      body: { ...entry, relations },
    });
    return true;
  } catch (err) {
    setStatus(dlg("status"), err.message, "error");
    return false;
  }
}

async function onEdgeSave() {
  if (!dlg("to").value.trim()) {
    setStatus(dlg("status"), "相手を入力してください", "error");
    dlg("to").focus();
    return;
  }
  const ok = await writeRelation({
    to: dlg("to").value,
    label: dlg("label").value,
    back: dlg("back").value,
    rank: dlg("rank").value,
    reveal: dlg("reveal").value,
    when: dlg("when").value,
  });
  if (!ok) return;
  edgeDialog.close();
  await refresh();
}

async function onEdgeRemove() {
  const who = dlg("who").textContent;
  if (!confirm(`関係「${who}」を削除します。よろしいですか？`)) return;
  if (!(await writeRelation(null))) return;
  edgeDialog.close();
  await refresh();
}

// --------------------------------------------------------------------------- //
// 読み込み
// --------------------------------------------------------------------------- //

let params = new URLSearchParams("");
let currentScope = "";
//: 絞り込む文書（ビューアから `?doc=` で渡ってくる）。空なら辞書全体。
//: **何に絞っているかは必ず画面に出す** —— 出さないと、辞書全体の図を
//: 「開いている文書の図」だと思われる（逆も同じ）
let currentDoc = "";
//: ビューアの上に重ねられているか。重ねているときは topbar を書き換えない
//: （戻るのは覆いを閉じるだけで、ページ移動ではない）
let embedded = false;

async function loadCategories() {
  const tree = await api("/api/categories").catch(() => []);
  const withEntries = tree.filter((n) => n.count > 0);
  categorySelect.replaceChildren(
    el("option", { value: "", text: "すべてのカテゴリ" }),
    ...withEntries.map((n) =>
      el("option", {
        value: `${n.scope}/${n.category}`,
        text: n.scope === "local" ? `📁 ${n.category}` : n.category,
      })
    )
  );
  const wanted = params.get("category");
  if (wanted) {
    const hit = withEntries.find(
      (n) => n.category === wanted && (!currentScope || n.scope === currentScope)
    );
    if (hit) categorySelect.value = `${hit.scope}/${hit.category}`;
  }
}

/** 何を出している図なのかを画面に書く（絞っているなら外す口も出す）。 */
function paintScope() {
  const note = document.getElementById("scopeNote");
  const all = document.getElementById("scopeAll");
  note.textContent = currentDoc
    ? `📄 「${currentDoc}」に出てくる語だけ`
    : "辞書全体を出しています";
  // 外すときも、カテゴリなど他の絞り込みは残す
  const rest = new URLSearchParams(params);
  rest.delete("doc");
  all.href = rest.toString() ? `/graph?${rest}` : "/graph";
  all.hidden = !currentDoc;

  // **戻り先も同じ文書にする。** ページとして開かれているとき、topbar の
  // 「ビューア」は素の `/` なので、押すと読んでいたものが消えて案内文に戻る、
  // と見える。重ねているときは触らない（戻るのは覆いを閉じるだけ）
  if (embedded) return;
  const back = document.querySelector('.topnav a[href="/"]');
  if (back && currentDoc) back.href = `/?open=${encodeURIComponent(currentDoc)}`;
}

function selection() {
  if (!categorySelect.value) return { category: null, scope: null };
  // カテゴリ名に空白は使えるが "/" は使えない。最初の "/" で 1 回だけ割る
  const cut = categorySelect.value.indexOf("/");
  return {
    scope: categorySelect.value.slice(0, cut),
    category: categorySelect.value.slice(cut + 1),
  };
}

/**
 * 図を画像として保存する口。**見せ方ごとに書かない**（どれも SVG を 1 枚返す）。
 *
 * 保存するのは**図の全体**（`contentBox`）で、いまの拡大率ではない ——
 * 拡大は見るための操作であって図の範囲ではないので、切れた図を保存しない。
 */
async function saveImage(kind) {
  if (!svgRoot || !contentBox) {
    setStatus(statusNode, "保存できる図がありません", "error");
    return;
  }
  setStatus(statusNode, "画像にしています", "busy");
  try {
    await saveGraph(svgRoot, contentBox, { kind, name: imageName() });
    setStatus(statusNode, "保存しました");
  } catch (err) {
    // **黙って諦めない。** 図は出ているのに保存だけできないことがある
    setStatus(statusNode, err.message, "error");
  }
}

/** 保存する名前。**何の図なのかを名前に残す**（あとで見分けられるように）。 */
function imageName() {
  const { category } = selection();
  const scope = MODE_WORDS[mode] || "相関図";
  const where = currentDoc ? currentDoc.split("/").pop() : (category || "辞書全体");
  return `相関図-${scope}-${where}`.replace(/[\\/:*?"<>|]/g, "-");
}

/**
 * 名指しされた語に目印を付けて、枠の中へ入れる（地図だけ）。
 *
 * 地図は座標が与えられている図なので、**中心の図のように組み替えられない** ——
 * 広い絵の隅に置かれていると、開いても自分の語がどこにあるか分からない。
 * 使うのは**乗せたときと同じ道具**（`.focusing` と `.lit`）なので、畳んだ名前も
 * 一緒に出る。**次に何かへ乗せれば普通に消える**（貼り付いたままにしない）。
 */
function spotlight(ref, { dim = true } = {}) {
  if (!ref || !svgRoot) return;
  const node = svgRoot.querySelector(`.rel-node[data-ref="${CSS.escape(ref)}"]`);
  if (!node) return;                  // その絵に置かれていない語（注意書きが出る）
  // **年表では暗くしない。** 地図は「広い絵のどこにあるか」を探す話なので周りを
  // 落として構わないが、年表で他の行を 0.12 にすると**前後関係そのものが読めなくなる**
  // （その語がいつなのかは、前後が見えて初めて分かる）。目印を付けて見せるだけにする
  if (dim) svgRoot.classList.add("focusing");
  node.classList.add("lit");
  bringIntoView(node);
}

function paintNotes(graph) {
  const lines = [];
  if (graph.outside) {
    // 絞ったぶんで落ちた辺。黙って欠けた図を出さない
    lines.push(
      `この文書に出てこない語との関係を ${graph.outside} 本伏せています` +
      "（相手も出てくる文書で見るか、辞書全体に戻してください）。"
    );
  }
  if (graph.hidden) {
    // 黙って伏せない。何本隠しているかは必ず出す
    lines.push(
      `判明位置が書かれた関係を ${graph.hidden} 本伏せています（上のチェックで出せます）。`
    );
  }
  if (graph.undated) {
    // 時系列に置き場所の無かった関係。数だけでも出す（黙って欠けさせない）
    lines.push(
      `この文書での位置が分からない関係が ${graph.undated} 本あります` +
      "（時系列ではいちばん下にまとめています）。"
    );
  }
  if (mode === "timeline" && timeRows === "edge") {
    // **並びを決め切ったことは書く。**書かないと、同じ時刻の中の順番が
    // 何で決まっているのか分からず、入力順だと思われる
    lines.push(
      "同じ時刻の中では、両端が同じカテゴリの関係を先に並べています"
      + "（事件どうしの筋が、顔ぶれの多さで下に埋もれないように）。"
    );
  }
  if (mode === "timeline" && timeRows === "node") {
    // **何を行にしているかは必ず書く。** 関係の並びと同じ縦軸に見えるので、
    // 書かないと「関係が減った」と読まれる（線ではなくチップになっただけ）
    lines.push(
      "1 行が 1 語です。軸に載るのは時刻（または読む位置）のある語だけで、"
      + "残りはその行のうしろに小さく並べています。"
      + "カテゴリで絞ると、行に出る語をそのカテゴリだけにできます。"
    );
    // **同じ時刻の中が何順かも書く**（関係の並びと同じ約束）。書かないと、
    // 同じ日付を書いた 2 件が並んだときに「並べ替えが効いていない」と読まれる
    // ——「本能寺の変」と「伊賀越え」に同じ日を書けば同着になるのが正しい
    lines.push(
      "同じ時刻のものは同じ帯にまとめ、その中は五十音順です"
      + "（前後を決めたいときは、時刻をより細かく書いてください）。"
    );
  }
  // **タブを出していないときは、押しのけの断りを出さない。** 用語ページの中では
  // その語の図が出ているのが当たり前で、戻す先のタブもここには無い
  // （「覚えているほうは変えていません」だけが浮く）
  if (centeredByUrl && mode === "ego" && showTabs) {
    // 覚えている見せ方を押しのけたことは書く（次にふつうに開けば元に戻る）
    lines.push(
      "語が名指しされているので、中心の図で開いています"
      + "（上のタブで戻せます。覚えているほうは変えていません）。"
    );
  }
  if (readingNote) lines.push(readingNote);
  if (modeFromUrl && mode === modeFromUrl && showTabs) {
    // URL が見せ方まで名指ししてきたとき。**同じ約束**（黙って押しのけない）
    lines.push(
      `開いたリンクの指定で${MODE_WORDS[mode] || "この見せ方"}にしています`
      + "（上のタブで戻せます。覚えているほうは変えていません）。"
    );
  }
  if (modeFellBack) {
    // 覚えていた見せ方を黙って別のものに差し替えない。**軸は 2 つある**ので、
    // どちらが足りないのかまで書く（「文書を開け」だけだと、時刻を書く道が見えない）
    lines.push(
      "時系列は「読む順」か「作中の時刻」のどちらかが要るので、いまは段の図に"
      + "しています（ビューアの「🕸 この文書の相関図」から開くか、語か関係に "
      + "when（例: 1560-05-19）を書くと出せます）。"
    );
  }
  if (axisFellBack) {
    // 選んだ軸が使えなかったので、もう片方で並べた。**覚えているほうは変えない**
    lines.push(
      axisFellBack === "when"
        ? "文書を開いていないので、作中の時刻で並べています"
          + "（覚えている軸は変えていません）。"
        : "作中の時刻が書かれた関係がないので、読む順で並べています"
          + "（関係に when を書くと、そちらで並べられます）。"
    );
  }
  if (mapFellBack) {
    // 同上。**「座標が無い」と言うところまでが約束**（黙って別の図にしない）
    lines.push(
      "地図に置ける語がこの範囲にないので、いまは段の図にしています" +
      "（エントリに map と pin を書くと出せます）。"
    );
  }
  for (const b of graph.broken) {
    lines.push(`「${b.from_term}」→「${b.to}」が解決できません: ${b.reason}`);
  }
  notes.hidden = !lines.length;
  notes.textContent = lines.join(" / ");
}

//: 何にも乗せていないときに枠へ出す案内。**空にしない** —— 空の帯が 1 本
//: 残っているだけだと、それが何の場所なのか分からない
const DETAIL_HINT = "線・マス・語に乗せる（またはキーボードで焦点を当てる）と、ここに詳しい内容が出ます。";

/** 図の下の枠に出す文を差し替える。空なら案内文に戻す（高さは変えない）。 */
function showDetail(text) {
  if (!detailNode) return;
  detailNode.textContent = text || DETAIL_HINT;
  detailNode.classList.toggle("is-hint", !text);
}

/**
 * 図の中のものに乗せたら、その説明を下の枠へ出す。
 *
 * **見せ方ごとに書かない。** 入れ物 1 つに仕掛けて `data-detail` を拾うだけに
 * してあるので、見せ方を足しても勝手に効く（属性を付け忘れたときは案内文に
 * 戻るだけで、壊れはしない）。
 */
function installDetail() {
  const pick = (ev) => showDetail(ev.target.closest?.("[data-detail]")?.dataset.detail);
  canvas.addEventListener("pointerover", pick);
  canvas.addEventListener("pointerleave", () => showDetail(""));
  // **キーボードも同じ。** ブラウザの吹き出しは焦点では出ないので、
  // ここが無いとキーボードだけの人には一言が最後まで読めない
  canvas.addEventListener("focusin", pick);
  canvas.addEventListener("focusout", () => showDetail(""));
}

/** 覚えている深さ。**読めない値は既定 (2) に落とす**（`ego.js` が決める）。 */
function rememberedDepth() {
  try {
    return normalizeDepth(localStorage.getItem(EGO_DEPTH_KEY));
  } catch {
    return DEFAULT_DEPTH;
  }
}

/** 覚えている絵。**読めなくても困らない** —— いちばん多く点が乗るものに落ちる。 */
function rememberedMap() {
  try {
    return localStorage.getItem(MAP_KEY) || "";
  } catch {
    return "";
  }
}

//: 覚えていた見せ方が「文書を開いているときだけ」のもので、いまは出せなかったか。
//: **黙って別の図を出さない**（何が起きたのかは注意書きに出す）
let modeFellBack = false;

//: 地図を選んでいたが、座標を書いた語が 1 つも無かったので落としたか。
//: 時系列の `modeFellBack` と同じ扱い（黙って差し替えない）
let mapFellBack = false;

//: どの絵を出しているか（``<scope>/<名前>``）。見せ方と同じで**覚えておく**
//: —— 覆いは何度でも開き直されるので、毎回選び直させない
const MAP_KEY = "glosspop.graphMap";
let mapName = "";

//: 地図で**チェックを外した語**（絵ごと）。**外したほうを覚える**のが肝 ——
//: 「出すもの」を覚えると、あとから足した語が黙って出なくなる
//: （`categories.reorder()` が送られなかったカテゴリを後ろに残すのと同じ考え方）
const MAP_HIDDEN_KEY = "glosspop.graphMapHidden";
let mapHidden = new Map();

//: 地図で**名前を消した絵**。AI に描かせた地図には地名が焼き込まれているのが
//: 普通なので、絵ごとに切れるようにする。**消したほうを覚える**（既定は出す側）。
//: 消しても情報は失われない —— 乗せれば図の下の枠と吹き出しに出る
const MAP_LABELS_KEY = "glosspop.graphMapNoLabels";
let mapNoLabels = new Set();

//: 地図で**用語の画像を点の代わりに出している絵**。名前とは逆に**出すほうを
//: 覚える** —— 点が絵になると図の見え方が大きく変わるので、既定は今までどおり丸
const MAP_FACES_KEY = "glosspop.graphMapFaces";
let mapFaces = new Set();

//: 地図を編集中か（丸を掴んで動かせる）。**覚えない** —— 開くたびに閲覧へ戻す。
//: 見せ方や絵と違って、うっかり動かすほうの害が大きい
let mapEditing = false;
//: 「次に絵を押したらここへ置く」語と**何として置くか**（`{ ref, term, kind }`）。
//: 置き終わったら `null` に戻す。**種別を持たせているのは、点の数から機械が
//: 決めないため** —— 3 点押したから領域、にはしない（書き方が種別の宣言そのもの、
//: という約束は画面から作るときも同じ）
let mapPlacing = null;
//: 次に置くときの種別。**覚えない**（`mapEditing` と同じ）が、続けて何本か
//: 引くことはあるので、この画面にいる間は選び直させない
let mapPlaceKind = "point";

//: 地図の時点（`null` は全部）。関係に書いた作中の時刻で巻き戻すと、
//: 進軍路が順に伸びる。**覚えない** —— 伏せたまま開き直すと「関係が消えた」と
//: 読まれる（「ここまで読んだぶん」と同じ約束）
let mapAt = null;

//: URL で語を名指しされたので、覚えている見せ方を押しのけて中心の図で開いたか。
//: これも黙ってやらない（同じ理由）
let centeredByUrl = false;

//: URL が見せ方まで名指ししてきたか（用語ページの「🗺 地図で見る」がそれ）。
//: **覚えているほうは書き換えない**のは `centeredByUrl` と同じで、押しのけた
//: ことは注意書きに出す。**`?ref=` より強い** —— 見せ方まで書いてある指定に
//: 「語が名指しされているから中心の図」を優先すると、押した先が別の図になる
let modeFromUrl = "";

//: URL で名指しされた語。地図では**その語を光らせて枠に入れる**（中心の図の
//: ように図そのものを組み替えられないので、代わりに目印を付ける）。
//: `egoCenter` と分けてあるのは、あちらが押すたびに移り変わるため
let namedRef = "";

/**
 * タブ専用の操作との仕切り。**出しているものがあるときだけ出す。**
 *
 * 出しっぱなしにすると、段の図のように専用の操作が 1 つも無いタブで行の末尾に
 * 意味のない線が残る（「何かが隠れている」と読まれる）。
 */
function syncTabTools() {
  const shown = [timeAxisPick, timeRowsPick, egoDepthPick, mapPick, mapEdit]
    .some((n) => n && !n.hidden);
  tabToolsSep.hidden = !shown;
}

/**
 * 見せ方を選び直す（タブを押した / ⋯ から地図を開いた）。
 *
 * **タブに無いもの（地図）もここを通す** —— 描き直し・断り書きの片付け・
 * 覚えるかどうかを 1 か所に集めておく（覚えない判断は `rememberMode()` の側）。
 */
function pickMode(value) {
  if (!ALL_MODES.includes(value) || value === mode) return;
  mode = value;
  // 自分で選び直したなら、落とした / 押しのけたときの断り書きはもう要らない
  modeFellBack = false;
  mapFellBack = false;
  centeredByUrl = false;
  modeFromUrl = "";
  rememberMode(mode);
  syncModeOptions();
  // 見せ方を変えるだけならサーバへ行き直さない（同じデータを描き替えるだけ）
  if (lastGraph) paintGraph(lastGraph);
}

/**
 * いまの選択をタブに映す。**一覧も同じ列に並ぶ**ので、正は `dict-tabs.js`。
 *
 * **ここでは落とさない。** 時系列が出せるかは軸で決まり、作中の時刻が
 * 書かれているかは**データを見ないと分からない** —— 実際に出せるかは `draw()`
 * が見て、駄目なら注意書きを出して段の図にする（地図とまったく同じ扱い）。
 * **覚えている選択は書き換えない**（一度出せなかっただけで設定が消えるのは驚く）。
 *
 * **タブを disabled にしないこと。** 押せない形にすると、なぜ押せないのかを
 * 書く場所が無くなる（いまは押せて、注意書きで「文書を開くか時刻を書く」と言える）。
 */
function syncModeOptions() {
  // **絞り込みは一覧へ渡す。** 「文学の図」から一覧へ戻ったら「文学の一覧」が出る
  paintDictTabs(modeTabs, { current: mode, onPick: pickMode, scope: selection() });
}

/**
 * 時系列で実際に使う軸。使えるものが無ければ空（＝時系列そのものが出せない）。
 *
 * **選んだほうを優先し、駄目ならもう片方に落とす**（落としたことは注意書きに
 * 出し、覚えているほうは書き換えない）。**「時刻が 1 本も無い」は普通に起きる**
 * ので、そのときに空の図を出さないための関門でもある。
 */
function axisFor(graph) {
  const canRead = !!currentDoc;
  const canWhen = hasWhen(graph);
  const wanted = TIME_AXES.includes(timeAxis) ? timeAxis : "read";
  if (wanted === "when" && canWhen) return "when";
  if (wanted === "read" && canRead) return "read";
  if (canWhen) return "when";
  if (canRead) return "read";
  return "";
}

/**
 * 作中の時刻で並べられるか。**「並べるもの」で見る先が変わる。**
 *
 * 語を並べるときは**語の時刻**が要る —— 辺だけを見ると、関係の書かれていない
 * 事件（日付は書いてある）が 1 件でも、「時刻が無い」と言って軸を落としてしまう。
 */
function hasWhen(graph) {
  if (timeRows === "node") {
    return (graph.nodes || []).some((n) => !n.outside && typeof n.when_at === "number");
  }
  return (graph.edges || []).some((e) => typeof e.when_at === "number");
}

/** 並べる軸の選択肢（時系列のときだけ出す）。使えない軸は選べなくする。 */
function paintAxisOptions(graph) {
  timeAxisPick.hidden = mode !== "timeline";
  timeRowsPick.hidden = mode !== "timeline";
  timeRowsPick.value = timeRows;
  if (timeAxisPick.hidden) return;
  const canWhen = hasWhen(graph);
  timeAxisPick.replaceChildren(
    el("option", {
      value: "read",
      text: "読む順",
      disabled: !currentDoc,
      title: "その文書のどこで読めるようになるか（文書を開いているときだけ）",
    }),
    el("option", {
      value: "when",
      text: "作中の時刻",
      disabled: !canWhen,
      title: "語と関係に書いた when の順（先頭の西暦で並べます）",
    }),
  );
  // 実際に描いた軸を映す（落ちたときは落ちた先が出る。覚えているほうは別）
  timeAxisPick.value = drawnAxis || timeAxis;
}

/**
 * 何つ先までの選択肢（中心の図のときだけ出す）。
 *
 * **選択肢を disabled にしない。** 深いほうを選んで近所がそこまで無くても、
 * 出していない語の数は今までどおり凡例に出る（タブを disabled にしないのと
 * 同じ約束で、押せない選択肢には理由を書く場所が無い）。
 */
function paintDepthOptions() {
  egoDepthPick.hidden = mode !== "ego";
  if (egoDepthPick.hidden) return;
  egoDepthPick.replaceChildren(
    ...DEPTHS.map((n) => el("option", {
      value: String(n),
      text: `${n} つ先まで`,
      title: n === 1
        ? "この語に直接書かれている関係だけ"
        : `中心から ${n} 本たどった先まで（遠いほど辞書全体に近づきます）`,
    })),
  );
  egoDepthPick.value = String(egoDepth);
}

//: 直前に描いたグラフ。見せ方を変えるだけならサーバへ行き直さない
let lastGraph = null;

//: 中心の図で真ん中に置く語。`?ref=` が初期値で、まわりの語を押すと移る。
//: **URL は書き換えない** —— 覆いとして重ねているとき、`location` は覆いが
//: 出しているものを指すとは限らない（`mount()` の引数で受けるのと同じ理由）
let egoCenter = "";

/** 中心を移して描き直す。**サーバへは行き直さない**（同じデータの描き替え）。 */
function moveCenter(ref) {
  if (!ref || ref === egoCenter) return;
  egoCenter = ref;
  if (lastGraph) paintGraph(lastGraph);
}

/** 受け取ったグラフを描いて、凡例と注意書きを添える。 */

/**
 * 地図の絵を入れる / 消すダイアログ。
 *
 * **顔と違って名前と一覧が要る**（地図は辞書に数枚ある）。一覧は
 * `/api/maps`（**置いてある絵**）で、`/api/graph` の `maps`（**出ている語が
 * 指している絵**）とは別物 —— 使っていない絵を消せるようにするため。
 *
 * **listener はダイアログを開く前に付ける。** 開いた瞬間から操作できるのに
 * 読み込みを待ってから付けると、その間の操作が黙って無視される（実際に 2 回踏んだ）。
 */
function installMapDialog() {
  const refs = {};
  for (const node of mapDialog.querySelectorAll("[data-ref]")) {
    refs[node.dataset.ref] = node;
  }
  const close = () => mapDialog.close();
  refs.close.addEventListener("click", close);
  mapEdit.addEventListener("click", () => {
    paintMapDialog(refs, null);
    mapDialog.showModal();
    loadMapDialog(refs);
  });
  refs.save.addEventListener("click", () => saveMapImage(refs));
  mapDialog.addEventListener("close", () => {
    // 絵が増減したら図も描き直す（サーバへは行き直す —— 一覧が変わっている）
    if (mapDialogChanged) {
      mapDialogChanged = false;
      refresh();
    }
  });
}

let mapDialogChanged = false;

async function loadMapDialog(refs) {
  setStatus(refs.status, "読み込み中", "busy");
  try {
    paintMapDialog(refs, await api("/api/maps"));
    setStatus(refs.status, "");
  } catch (err) {
    setStatus(refs.status, err.message, "error");
  }
}

/** 一覧と保存先を描く。**開いた時点で持っているものを描き、届いたら描き直す。** */
function paintMapDialog(refs, data) {
  const maps = data?.maps || [];
  refs.list.replaceChildren(
    ...(maps.length
      ? maps.map((m) => el("div", { class: "cat-row" }, [
        el("img", { src: m.url, alt: "", class: "map-thumb" }),
        el("span", { class: "cat-row-main", text: `${m.name}（${m.scope === "local" ? "📁 このフォルダ" : "全体"}・${Math.round(m.bytes / 1024)} KB）` }),
        el("button", {
          type: "button",
          class: "ghost",
          text: "消す",
          onclick: () => deleteMapImage(refs, m),
        }),
      ]))
      : [el("p", { class: "empty", text: data ? "まだ絵がありません。" : "読み込み中…" })])
  );
  const canLocal = data?.can_local !== false;
  refs.scope.replaceChildren(
    el("option", { value: "global", text: "全体" }),
    // **辞書の無いフォルダには置けない**（開いただけのフォルダを汚さない）
    el("option", { value: "local", text: "📁 このフォルダ", disabled: !canLocal }),
  );
}

async function saveMapImage(refs) {
  const name = refs.name.value.trim();
  const file = refs.file.files?.[0];
  if (!name) return setStatus(refs.status, "名前を入れてください", "error");
  if (!file) return setStatus(refs.status, "画像を選んでください", "error");
  setStatus(refs.status, "送っています", "busy");
  try {
    const url = `/api/map?scope=${encodeURIComponent(refs.scope.value)}`
      + `&name=${encodeURIComponent(name)}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    mapDialogChanged = true;
    refs.name.value = "";
    refs.file.value = "";
    paintMapDialog(refs, data);
    setStatus(refs.status, "入れました");
  } catch (err) {
    setStatus(refs.status, err.message, "error");
  }
}

async function deleteMapImage(refs, item) {
  // **エントリの map は書き換えない**ので、消すと出なくなるだけ。そう伝える
  if (!confirm(`「${item.name}」を消します。この絵に置いていた語は地図に出なくなります（辞書は消えません）。よろしいですか？`)) return;
  setStatus(refs.status, "消しています", "busy");
  try {
    const url = `/api/map?scope=${encodeURIComponent(item.scope)}`
      + `&name=${encodeURIComponent(item.name)}`;
    mapDialogChanged = true;
    paintMapDialog(refs, await api(url, { method: "DELETE" }));
    setStatus(refs.status, "消しました");
  } catch (err) {
    setStatus(refs.status, err.message, "error");
  }
}


/**
 * 動かした形を書き戻す。**専用の口を使う**（`PUT /api/map-shape/{ref}`）。
 *
 * 相関図が持っているのはノードの一部だけなので、ここから `EntryDraft` を組み立てて
 * PUT すると**本文も関係も落ちる** —— サーバ側で読み直して差し替えさせる
 * （関係の書き込みを `/api/relations` にまとめたのと同じ理由）。
 */
async function saveMapShape(ref, kind, points, { remember = true } = {}) {
  if (remember) rememberShape(ref);
  // **触っていた場所を先に控える。** 下の `refresh()` が図ごと差し替えるので、
  // ここで控えないと戻す手掛かりが消える（描き直したあとでは遅い）
  const spot = focusedMapControl();
  try {
    await api(`/api/map-shape/${encodePath(ref)}`, {
      method: "PUT",
      body: { kind, points },
    });
    setStatus(statusNode, "地図の位置を保存しました");
    await refresh();
    restoreMapFocus(spot);
  } catch (err) {
    setStatus(statusNode, err.message, "error");
  }
}

/**
 * いま焦点が当たっている地図の操作部。**保存の前に控える。**
 *
 * 地図は**保存が即時**（掴んで離したら書く）で、書くたびに `refresh()` が
 * 図を差し替える —— 焦点を持っていた頂点の丸はそこで消える。マウスは
 * 1 ドラッグ ＝ 1 保存なので気付けないが、**キーボードでは 1 押しごとに
 * 焦点が body へ落ち、2 回目以降が効かない**（実測。矢印キーで 1 回しか
 * 動かせず、＋ と Delete も 1 操作ごとに Tab で戻る必要があった）。
 * 「掴めない人を締め出さない」と書いてある側だけが効いていない状態だった。
 */
function focusedMapControl() {
  const node = document.activeElement;
  if (!node) return null;
  if (node.classList?.contains("rel-map-handle") && node.dataset.ref) {
    return { ref: node.dataset.ref, vertex: Number(node.dataset.vertex) || 0 };
  }
  // ↩ は続けて押す（何手も遡る）ボタンなので、ここも戻す
  if (node.dataset?.ref === "mapUndo") return { undo: true };
  // 種別を変えた直後は、その語の頂点を触りに行く番 —— 一覧の頭へ落とさない
  if (node.dataset?.ref === "mapKind") return { kind: node.dataset.item };
  return null;
}

/**
 * 控えた場所へ焦点を戻す。**無くなっていたら何もしない。**
 *
 * 頂点は増えたり減ったりする（＋ / Delete）ので、**同じ添字**へ戻して
 * 端で丸める —— 消したときは「次の頂点」、足したときは「押した頂点」に残る。
 * どちらも押した場所の続きなので、キーボードで連続して操作できる。
 * **「消えたら先頭へ」にはしない**（図の反対側へ飛ぶと、どこを触っていたか
 * 分からなくなる）。
 */
function restoreMapFocus(spot) {
  if (!spot) return;
  if (spot.undo) {
    mapLayers.querySelector("[data-ref=mapUndo]")?.focus();
    return;
  }
  if (spot.kind) {
    mapLayers.querySelector(
      `[data-ref=mapKind][data-item="${CSS.escape(spot.kind)}"]`
    )?.focus();
    return;
  }
  const dots = [...(svgRoot?.querySelectorAll(
    `.rel-map-handle[data-ref="${CSS.escape(spot.ref)}"]`
  ) || [])];
  if (!dots.length) return;                 // 地図から下ろした / 種別が変わった
  dots[Math.min(Math.max(spot.vertex, 0), dots.length - 1)].focus();
}

//: 書き換える**前**の形。**保存が即時**（掴んで離したら書く）なので、
//: 戻す手段が無いと**消した頂点は取り返せない**（→ docs/open-questions.md で
//: 「頂点を消せるようにするなら先に 1 つ戻すを決める」としていた宿題）。
//:
//: **同じ語を続けて触ったぶんは 1 つにまとめる。** 矢印キーは 1 押しごとに
//: 保存するので、押した回数だけ積むと「戻す」を何十回も押すことになる ——
//: 積むのは**その語を最初に触る前の形**。
const MAX_UNDO = 50;
let mapUndo = [];

/** いまの形を控える。**まだ置かれていない語は「置かれていない」ことを控える。** */
function rememberShape(ref) {
  if (mapUndo[mapUndo.length - 1]?.ref === ref) return;
  const node = (lastGraph?.nodes || []).find((n) => n.ref === ref);
  if (!node) return;
  mapUndo.push({
    ref,
    term: node.term || ref,
    // 置かれていなかったなら空の種別で戻す（＝地図から下ろす）
    kind: node.shape?.kind || "",
    points: (node.shape?.points || []).map((p) => [...p]),
  });
  if (mapUndo.length > MAX_UNDO) mapUndo.shift();
}

/** 1 つ戻す。**戻した操作は積み直さない**（押すたびに 1 つずつ遡る）。 */
async function undoMapShape() {
  const prev = mapUndo.pop();
  if (!prev) return;
  await saveMapShape(prev.ref, prev.kind, prev.points, { remember: false });
  setStatus(statusNode, `「${prev.term}」の形を戻しました`);
}

/** 覚えている「外したもの」を読む。読めなければ空（＝全部出す側に倒す）。 */
function rememberedHidden() {
  try {
    const raw = JSON.parse(localStorage.getItem(MAP_HIDDEN_KEY) || "{}");
    return new Map(Object.entries(raw).map(([k, v]) => [k, new Set(v)]));
  } catch {
    return new Map();
  }
}

function rememberedNoLabels() {
  try {
    return new Set(JSON.parse(localStorage.getItem(MAP_LABELS_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

/** 覚えている並べるもの。読めない値は「関係」に落ちる（既定を壊さない）。 */
function rememberedRows() {
  try {
    const saved = localStorage.getItem(TIME_ROWS_KEY);
    return TIME_ROWS.includes(saved) ? saved : "node";
  } catch {
    return "node";
  }
}

/** 覚えている並べる軸。読めない値は「読む順」に落ちる（起動できなくならないこと）。 */
function rememberedAxis() {
  try {
    const saved = localStorage.getItem(TIME_AXIS_KEY);
    return TIME_AXES.includes(saved) ? saved : "read";
  } catch {
    return "read";
  }
}

function rememberedFaces() {
  try {
    return new Set(JSON.parse(localStorage.getItem(MAP_FACES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveHidden() {
  try {
    const plain = {};
    for (const [key, set] of mapHidden) if (set.size) plain[key] = [...set];
    localStorage.setItem(MAP_HIDDEN_KEY, JSON.stringify(plain));
  } catch {
    /* 使えない環境でも、その画面では効く */
  }
}

/**
 * 地図に出すものの一覧（チェック）。**地図のときだけ出す。**
 *
 * **一覧は「その絵に置ける語」全部**（外したものも並べる）—— 外したものが消えると
 * 戻す手段が無くなる。**カテゴリで束ねて並べる**ので「説だけ全部外す」ができる。
 *
 * **外したほうを覚える。** 「出すもの」を覚えると、あとから足した語が黙って
 * 出なくなる（新しく書いた語は必ず出る側に倒す）。
 */
/**
 * 種別を選び直す口（編集中だけ）。**種別は人が宣言する。**
 *
 * 「最初と最後が同じなら領域」のような推測をしないのと同じ話で、点の数が増えた
 * ／減ったから種別が変わる、にはしない（→ CLAUDE.md）。足りない点は
 * `fitToKind()` が足し、余る点（線 → 点）は落とす —— **落ちることは押す前に出す。**
 */
function kindPicker(item) {
  const pick = el("select", {
    class: "auto-width",
    "data-ref": "mapKind",
    // 選ぶと保存 → 描き直しで一覧ごと作り直される。**戻す手掛かり**
    // （頂点の丸と同じ話。→ `focusedMapControl`）
    "data-item": item.ref,
    "aria-label": `${item.term} の形`,
    title: "この語の形。線や領域にすると、足りない点は隣に足されます",
  }, [
    ...Object.entries(KIND_WORDS).map(([value, text]) =>
      el("option", { value, text, selected: value === item.kind })),
    // **地図から下ろす道もここに置く。** 座標を消す口が画面に無いと、
    // 「置いてしまったものを取り消す」が frontmatter を開く作業になる
    // （点は頂点を 1 つも消せないので、その受け皿でもある）
    el("option", { value: "", text: "地図から下ろす" }),
  ]);
  pick.value = item.kind;
  pick.addEventListener("change", () => {
    const node = (lastGraph?.nodes || []).find((n) => n.ref === item.ref);
    const points = node?.shape?.points || [];
    const next = pick.value ? fitToKind(pick.value, points) : [];
    // **落ちるものは押す前に出す**（削除・統合と同じ扱い）。戻せるようには
    // したが、消えることを黙って通してよい理由にはならない
    const ask = !pick.value
      ? `「${item.term}」を地図から下ろします（座標が消えます）。よろしいですか？`
      : next.length < points.length
        ? `「${item.term}」を${KIND_WORDS[pick.value]}にすると、`
          + `${points.length - next.length} 点が落ちます。よろしいですか？`
        : "";
    if (ask && !confirm(ask)) {
      pick.value = item.kind;                 // 断られたら選択も戻す
      return;
    }
    saveMapShape(item.ref, pick.value, next);
  });
  return pick;
}

/**
 * 時点のスライダを出す（地図で、時点が 2 つ以上あるときだけ）。
 *
 * **顔ぶれが変わったときだけ作り直す。** 毎回作り直すと、**掴んでいる最中に
 * スライダごと消えて**ドラッグが切れる（保存のたびに焦点が飛んでいたのと同じ形）。
 * 止まれる時点が 1 つなら動かしても何も変わらないので、出さない。
 */
function paintMapTime(drawn) {
  const times = drawn.times || [];
  mapTimeBar.hidden = mode !== "map" || times.length < 2;
  if (mapTimeBar.hidden) {
    mapTimeKey = "";
    return;
  }
  const key = `${mapName}|${times.map((t) => t.at).join(",")}`;
  if (key === mapTimeKey) return;      // 同じ顔ぶれ。掴んでいる最中かもしれない
  mapTimeKey = key;
  mapTimeBar.replaceChildren(...timeSlider(times));
}

/**
 * 時点のスライダ（地図。関係に作中の時刻が書いてあるときだけ）。
 *
 * **いちばん右が「全部」で、そこが既定。** 左へ動かすとその時点までに起きた
 * 関係だけが残る —— 進軍路が順に伸びるのはこれ。
 *
 * 守ること 4 つ:
 *
 * - **止まれるのは実際にある時点だけ**（図が変わらない目盛りを並べない）
 * - **時刻の無い関係は常に出す。** 伏せる側に倒すと、時刻を 1 つ書いただけで
 *   **それ以外が全部消えた**ように見える（時刻の無い関係のほうが普通）
 * - **覚えない。** 開くたびに全部へ戻す（伏せたまま開き直すと「関係が消えた」と
 *   読まれる。「ここまで読んだぶん」と同じ約束）
 * - **図の外に置く。** 図の中に入れると拡大縮小と移動に巻き込まれる
 */
function timeSlider(times) {
  // 覚えていた時点がこの絵に無ければ「全部」に戻す（絵を切り替えたときなど）
  let index = times.findIndex((t) => t.at === mapAt);
  if (index < 0) {
    mapAt = null;
    index = times.length;
  }
  const range = el("input", {
    type: "range",
    class: "map-time",
    "data-ref": "mapTime",
    min: "0",
    max: String(times.length),
    value: String(index),
    "aria-label": "時点",
    title: "いちばん右が全部。左へ動かすと、その時点までの関係だけを出します",
  });
  const label = el("span", {
    class: "hint",
    "data-ref": "mapTimeLabel",
    text: index >= times.length ? "全部" : times[index].label,
  });
  range.addEventListener("input", () => {
    const i = Number(range.value);
    mapAt = i >= times.length ? null : times[i].at;
    // 文字だけ先に追いつかせる（描き直しは下で走るが、掴んで動かす間も出したい）
    label.textContent = i >= times.length ? "全部" : times[i].label;
    if (lastGraph) paintGraph(lastGraph);
  });
  return [el("span", { class: "hint", text: "時点:" }), range, label];
}

/**
 * 用語の画像を点の代わりに出すかの切り替え（絵ごとに覚える）。
 *
 * **既定は出さない。** 名前（既定は出す）と逆にしてあるのは、点が絵になると
 * 図の見え方が大きく変わるから —— 頼まれていないのに変えない。
 * **画像を持つ語が 1 つも無いときは呼ぶ側が出さない**（→ `paintMapLayers`）。
 */
function faceToggle() {
  const box = el("input", { type: "checkbox" });
  box.checked = mapFaces.has(mapName);
  box.addEventListener("change", () => {
    if (box.checked) mapFaces.add(mapName);
    else mapFaces.delete(mapName);
    try {
      localStorage.setItem(MAP_FACES_KEY, JSON.stringify([...mapFaces]));
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    if (lastGraph) paintGraph(lastGraph);
  });
  return el("label", {
    class: "check",
    "data-ref": "mapFaces",
    title: "用語ごとに入れた画像を、点の代わりに出す（無い語は丸のまま）",
  }, [box, el("span", { text: "用語の画像" })]);
}

/**
 * 次に置く形を選ぶ口（置き待ちの語を押す前）。
 *
 * **種別は人が宣言する。** 押した点の数から機械が決めない（3 点押したから領域、
 * にはしない）—— `kindPicker()` が「置いたあとで変える」側で、こちらが
 * 「置く前に決める」側。**どちらも同じ約束の上にいる。**
 */
function placeKindPicker() {
  const pick = el("select", {
    class: "auto-width",
    "data-ref": "mapPlaceKind",
    "aria-label": "次に置く形",
    title: "点は 1 回押すだけ。線と領域は、押した数だけ点が増えます",
  }, Object.entries(KIND_WORDS).map(([value, text]) =>
    el("option", { value, text, selected: value === mapPlaceKind })));
  pick.value = mapPlaceKind;
  // **描き直さない。** 選んだだけでは何も起きない（押してから効く）ので、
  // ここで図を作り直すと、そのぶん点滅するだけになる
  pick.addEventListener("change", () => {
    mapPlaceKind = pick.value;
  });
  return pick;
}

/**
 * 置いている最中の行（確定 / やめる）。
 *
 * **Enter と Esc だけにしない。** 発見できないうえ、この地図は「掴んで置く」
 * 道具なので、手だけで完結できないと途中で止まる（✕ を右クリックにしなかった
 * のと同じ理由）。**点は押した時点で終わる**ので、確定は出さない。
 */
function placingRow(place) {
  const row = [el("span", {
    class: "hint",
    text: place.kind === "point"
      ? `「${place.term}」を置きます: 絵の上を押してください`
      : `「${place.term}」を${KIND_WORDS[place.kind]}として置いています:`,
  })];
  if (place.kind !== "point") {
    row.push(el("button", {
      type: "button",
      class: "chip primary",
      "data-ref": "mapPlaceDone",
      text: "✓ 確定",
      title: "ここまでに押した点で決める（Enter でも）",
      onclick: place.finish,
    }));
  }
  row.push(el("button", {
    type: "button",
    class: "chip",
    "data-ref": "mapPlaceCancel",
    text: "やめる",
    title: "置くのをやめる（Esc でも）",
    onclick: place.cancel,
  }));
  return row;
}

function paintMapLayers(drawn) {
  const items = drawn.items || [];
  mapEdit.hidden = mode !== "map";
  // **置ける語が 1 つも無くても出す** —— 「置く」から未配置の語へ行けるので、
  // ここを隠すと最初の 1 つを置く道が無くなる（🖼 絵 を隠さないのと同じ話）
  mapLayers.hidden = mode !== "map" || !(items.length || (drawn.pending || []).length);
  if (mapLayers.hidden) return;
  const off = mapHidden.get(mapName) || new Set();

  const toggle = (refs, on) => {
    const set = new Set(mapHidden.get(mapName) || []);
    for (const ref of refs) {
      if (on) set.delete(ref);
      else set.add(ref);
    }
    mapHidden.set(mapName, set);
    saveHidden();
    if (lastGraph) paintGraph(lastGraph);
  };

  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }

  // **名前を出すかの切り替え。** 絵に地名が焼き込まれていると二重になるので、
  // 絵ごとに切れるようにする（既定は出す側）
  const nameBox = el("input", { type: "checkbox" });
  nameBox.checked = !mapNoLabels.has(mapName);
  nameBox.addEventListener("change", () => {
    if (nameBox.checked) mapNoLabels.delete(mapName);
    else mapNoLabels.add(mapName);
    try {
      localStorage.setItem(MAP_LABELS_KEY, JSON.stringify([...mapNoLabels]));
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    if (lastGraph) paintGraph(lastGraph);
  });
  // **編集は覚えない。** 開くたびに閲覧へ戻す（うっかり動かすほうの害が大きい）
  const editBox = el("input", { type: "checkbox" });
  editBox.checked = mapEditing;
  editBox.addEventListener("change", () => {
    mapEditing = editBox.checked;
    mapPlacing = null;
    if (lastGraph) paintGraph(lastGraph);
  });
  const parts = [
    el("label", {
      class: "check",
      "data-ref": "mapEdit",
      title: "丸を掴んで位置を動かせるようにする（離すと保存されます）",
    }, [editBox, el("span", { text: "置く" })]),
  ];
  // **保存は即時なので、戻す道を必ず出す。** 消した頂点はこれが無いと
  // 取り返せない（→ docs/design-notes.md）。**編集中だけ**出す
  if (mapEditing && mapUndo.length) {
    const last = mapUndo[mapUndo.length - 1];
    parts.push(el("button", {
      type: "button",
      class: "chip",
      "data-ref": "mapUndo",
      text: `↩ 「${last.term}」を戻す`,
      title: `${mapUndo.length} 手ぶん戻せます（1 回押すと 1 つ）`,
      onclick: undoMapShape,
    }));
  }
  parts.push(
    el("label", {
      class: "check",
      "data-ref": "mapNames",
      title: "絵に地名が入っているときは外す（乗せれば名前は出ます）",
    }, [nameBox, el("span", { text: "名前を出す" })]),
  );
  // **画像を持つ語が 1 つもなければ出さない。** ここは画像を入れる口ではない
  // （入れるのは用語ページ）ので、押しても何も起きない切り替えを並べても
  // 迷わせるだけ —— 「絵が無くても 🖼 絵 は出す」（鶏と卵）とは事情が違う
  if (items.some((item) => item.hasImage)) parts.push(faceToggle());
  parts.push(el("span", { class: "hint", text: "地図に出すもの:" }));
  for (const [category, list] of groups) {
    const refs = list.map((i) => i.ref);
    const allOn = refs.every((r) => !off.has(r));
    // カテゴリの見出しそのものがまとめて切り替えるボタン（「説だけ外す」ができる）。
    // **半端に選ばれているときは「全部出す」に倒す** —— 迷ったら出す側、が
    // この辞書の約束（黙って欠けさせない）。全部出ているときだけ全部外す
    parts.push(el("button", {
      type: "button",
      class: "chip",
      text: `${category || "未分類"} ${refs.filter((r) => !off.has(r)).length}/${refs.length}`,
      title: allOn ? "このカテゴリをまとめて外す" : "このカテゴリをまとめて出す",
      onclick: () => toggle(refs, !allOn),
    }));
    for (const item of list) {
      const box = el("input", { type: "checkbox" });
      box.checked = !off.has(item.ref);
      box.addEventListener("change", () => toggle([item.ref], box.checked));
      // **色は図が決めたものをそのまま出す**（ここで作り直さない）。線と領域は
      // 同じ絵の上で重なるので色で分けてあり、その色を一覧にも出しておかないと
      // 「どの線がどれか」を目で追うことになる。点には色が無い（重ならない）
      const swatch = item.color
        ? el("span", {
          class: "map-swatch",
          style: `background: ${item.color}`,
          "aria-hidden": "true",
        })
        : null;
      parts.push(el("label", { class: "check", "data-ref": "mapItem" },
        [box, swatch, el("span", { text: item.term })]));
      // **種別は編集中だけ選べる。** ここが**画面から線や領域を作る唯一の道**
      // （置くと点になるので、線にしたければ種別を変えて頂点を足す）。
      // **点の数から機械が推測しない** —— 足りないぶんは `fitToKind()` が足す。
      // **label の中には入れない** —— 中の `<select>` を押したときに
      // チェックまで動くかがブラウザまかせになる（隣に並べれば起きない）
      if (mapEditing) parts.push(kindPicker(item));
    }
  }
  // **まだ置いていない語**（絵の名前だけ書いてある）。押してから絵を押すと置く。
  // 分類していない —— 「この絵に置きたい」と書いてあるものだけが並ぶ
  const pending = drawn.pending || [];
  if (mapEditing && drawn.place) {
    // 置いている最中は、**その語のことだけ**を出す（一覧を出したままだと、
    // 押しかけの形を捨てて別の語へ移れてしまう）
    parts.push(...placingRow(drawn.place));
  } else if (mapEditing && pending.length) {
    parts.push(
      el("span", { class: "hint", text: "まだ置いていない:" }),
      // **種別は押す前に選ぶ。** ここが「線や領域を最初から作る」唯一の道で、
      // 置いてから種別を変える道（`kindPicker`）も今までどおり残してある
      placeKindPicker(),
    );
    for (const item of pending) {
      parts.push(el("button", {
        type: "button",
        class: "chip",
        "data-ref": "mapPending",
        text: item.term,
        title: "押してから絵の上を押すと、そこへ置きます",
        onclick: () => {
          mapPlacing = { ref: item.ref, term: item.term, kind: mapPlaceKind };
          if (lastGraph) paintGraph(lastGraph);
        },
      }));
    }
  }
  mapLayers.replaceChildren(...parts);
}

/** 絵の選択肢を作る。**地図のとき、絵が 2 枚以上あるときだけ出す。** */
function paintMapOptions(graph) {
  const maps = graph.maps || [];
  mapPick.hidden = mode !== "map" || maps.length < 2;
  if (mapPick.hidden) return;
  mapPick.replaceChildren(
    ...maps.map((m) => el("option", { value: `${m.scope}/${m.name}`, text: m.name }))
  );
  mapPick.value = mapName;
}

/**
 * 「ここまで読んだぶん」に絞る。**出てきていない語と、その語につながる関係を伏せる。**
 *
 * 判断は `reading.js` に任せる（本文のリンクそのものを見る）—— ここで文字位置と
 * ブロックの添字を突き合わせようとしないこと。**伏せた数は必ず返す**ので、
 * 図は「黙って欠けた図」にならない（`hidden` / `outside` と同じ約束）。
 */
function limitToRead(graph) {
  const now = readingNow();
  if (!now) return { graph, note: "" };
  const nodes = graph.nodes.filter((n) => now.refs.has(n.ref));
  const shown = new Set(nodes.map((n) => n.ref));
  const edges = graph.edges.filter((e) => shown.has(e.from) && shown.has(e.to));
  const note = [
    `いま読んでいるところまでに出てきた ${nodes.length} 語だけを出しています`,
    graph.nodes.length - nodes.length
      ? `（まだ出てきていない ${graph.nodes.length - nodes.length} 語と、`
        + `その語につながる関係 ${graph.edges.length - edges.length} 本は伏せています`
      : "（伏せたものはありません",
    // **決めきれない語は入れていない、と書く。** 黙って落とすと「出てきたのに
    // 図に無い」になり、機械の取りこぼしと見分けが付かない
    now.undecided
      ? `。同じ表記が複数のカテゴリにある語 ${now.undecided} か所は、どれのことか`
        + "決まらないので入れていません"
      : "",
    "）。",
  ].join("");
  return { graph: { ...graph, nodes, edges }, note };
}

function paintGraph(graph) {
  lastGraph = graph;
  showDetail("");
  // **絞る前のものを覚えておく**（チェックを外したらサーバへ行き直さずに戻す）
  const limited = readSoFarCheck.checked ? limitToRead(graph) : { graph, note: "" };
  readingNote = limited.note;
  graph = limited.graph;
  const drawn = draw(graph);
  paintAxisOptions(graph);
  paintDepthOptions();
  paintMapOptions(graph);
  paintMapTime(drawn);
  paintMapLayers(drawn);
  // **タブも描き直す。** 一覧へ戻るリンクにいまの絞り込みを載せるため —— `mount()`
  // の 1 回だけだと、カテゴリの選択肢がまだ読めていないので素の `/glossary` になる
  // （「文学の図」から一覧へ戻ったのに全部出る、という食い違いになっていた）
  syncModeOptions();
  syncTabTools();
  paintNotes(graph);
  setStatus(statusNode, `${graph.nodes.length} 語 / ${graph.edges.length} 本の関係`);
  const common =
    "→ は一方的、⇄ は相互。破線の枠はまだ登録されていない語で、押すと辞書で探せます。"
    + (mode === "matrix" ? "マスを押すとその関係を直せます。" : "線を押すとその関係を直せます。")
    + "図はドラッグで動かし、ホイールで拡大縮小できます（右下のボタンと、"
    + "図を選んでからの ← ↑ ↓ → ＋ − 0 でも）。"
    + "語に乗せると、その語の関係だけが濃く出ます。";
  // **見せ方が違えば読み方の説明も違う。** 同じ文言を出すと、交差しない図でも
  // 「上下は段」を探すことになる（どちらも上下は保っているが、形が違う）。
  // **ここは画面にそのまま出る文なので `**` で囲まないこと**（太字にする手段が
  // 無いぶん、記号がそのまま読まれる。実際にそうなっていた）
  const shape = {
    fabric:
      "用語が横線、関係が縦線です。関係ごとに列が分かれているので線どうしは交差しません。"
      + "上下の関係は行の並びで表しています（上にあるものが上位）。",
    // 「無い」が見えるのがこの見せ方の役目。そう書かないと、ただのまばらな格子に見える
    matrix:
      "行が「から」、列が「へ」です。線を引かないので交差しません。"
      + "空きマスは、まだ関係を書いていない組です。対角を挟んで両側が埋まっていれば相互。"
      + "太い線はカテゴリの切れ目。上下の関係は行の並びで表しています（上にあるものが上位）。",
    // 規模に依らないのがこの見せ方の役目。何語の辞書から切り出した近所なのかは
    // `drawn.note` が出す（この図だけを見て「関係はこれで全部」と読ませない）
    // **何つ先までかはタブの下で選べる**ので、環の説明も選んだ深さで言う
    // （「外側が 2 つ先」と決め打つと、3 つ先を選んだ人には嘘になる）
    ego:
      `真ん中の語から ${drawn.depth || egoDepth} つ先までを環に並べています`
      + "（内側の環ほど近い相手です。何つ先までかはタブの下で選べます）。"
      + "まわりの語を押すとそこが中心に移り、"
      + "真ん中の語を押すと辞書ページが開きます。"
      + "上下の関係は置き場所で表しています（上にあるものが上位、対等は左右）。",
    // 「いつ」が見えるのがこの見せ方の役目。位置は毎回その場で計算していて
    // 保存はしていない、と書いておかないと「編集したらずれる」と読まれる
    // **どちらの軸で並べているかで説明が変わる。** 同じ縦軸に見えるので、
    // 書かないと「読む順」と「作中の時刻」を取り違える（そもそも一致しない）
    timeline: timeRows === "node"
      ? (drawnAxis === "when"
        ? "上から順に、作中で起きた順に「語」を並べています（語に書いた when の、先頭の西暦）。"
          + "行どうしの関係は右の線、軸に載らない相手は行のうしろの小さな枠です（押すと開けます）。"
          + "人物に時刻を書かないのは、期間（生没）だからです —— そのぶん自然に行のうしろへ回ります。"
        : "上から順に、この文書に出てくる順に「語」を並べています。"
          + "左の見出しはその語が初めて出てくる位置（章・ページ・行）。"
          + "位置は開くたびに本文から数えていて保存はしません。")
      : drawnAxis === "when"
      ? "上から順に、作中で起きた順です（語と関係に書いた when の、先頭の西暦で並べています）。"
        + "左の見出しは書かれたままの文字列なので、うしろに元号や作中の暦を書けます。"
        + "時刻を書いていない関係と、西暦として読めない関係は、いちばん下の帯にまとめています。"
        + "「判明: …」は読者がいつ知るかで、こちらの並べ替えには使っていません。"
      : "上から順に、この文書を読み進めると関係が読めるようになる順です。"
      + "左の見出しは、両方の語が出そろう位置（章・ページ・行）。"
      + "位置は開くたびに本文から数えていて保存はしないので、本文を直せば次に開いたときに追いつきます。"
      + "「判明: …」は人が書いた判明位置で、並べ替えには使っていません。",
    // 「どこ」が見えるのがこの見せ方の役目。**分類していない**と書いておかないと、
    // 「地名なのに出てこない」を機械の取りこぼしだと読まれる
    map:
      "座標を書いた語を絵の上に置いています。"
      + "どれが地名かは決めていません —— 座標を書いた語が出るだけなので、"
      + "出したい語には map と pin を書いてください。"
      + "線は、両端がこの絵に置かれている関係だけです。"
      // **色の意味は書く。** 書かないと「カテゴリごとの色」だと読まれる
      + "経路（線）と領域は、同じ絵の上で重なるので 1 つずつ色を分けています"
      + "（上の一覧に出ている色と同じ）。点はどれも同じ色です。",
  }[mode] || "▲▼ の代わりに上下の関係は段で表しています。";
  legend.textContent =
    shape + common
    // **出していないものを見せ方が数えていれば、それも書く**（何を切り出した
    // 図なのかが分からないと、「関係はこれで全部」と読まれる）
    + (drawn.note || "")
    // **畳んだことは書く。** 黙って出さないと「一言が書かれていない関係」と
    // 見分けが付かない（隠した本数を必ず返すのと同じ約束）
    + (drawn.tucked
      ? `重なって置けない一言 ${drawn.tucked} 本は畳んでいます（線かその語に乗せると出ます）。`
      : "")
    // **段の外に出したことは書く。** 黙って別のところへ置くと、上下の並びを
    // 読んでいる人には「下にあるから下位」に見える
    + (drawn.lonely && drawn.linked
      ? `関係が 1 本も書かれていない ${drawn.lonely} 語は、`
        + (mode === "fabric" ? "区切り線の下" : "段の外（区切り線の下）")
        + "に並べています。"
      : "");
}

async function refresh() {
  const { category, scope } = selection();
  setStatus(statusNode, "読み込み中", "busy");
  const query = new URLSearchParams();
  if (category) query.set("category", category);
  if (scope) query.set("scope", scope);
  if (spoilerCheck.checked) query.set("spoilers", "true");
  if (currentDoc) query.set("doc", currentDoc);
  try {
    paintGraph(await api(`/api/graph?${query}`));
  } catch (err) {
    lastGraph = null;
    setStatus(statusNode, err.message, "error");
    canvas.replaceChildren(el("p", { class: "status error", text: err.message }));
  }
}

/**
 * 相関図を ``host`` に描く。
 *
 * `/graph` を直接開いたときも、ビューアの上に重ねるときも、ここを通る。
 * **入れ物と URL は外から渡す** —— `location` を直接読むと、重ねたときに
 * 「いま覆いが出しているもの」と食い違う。
 */
export async function mount(host, { search = "", embed = false, tabs = true } = {}) {
  host.innerHTML = TEMPLATE;
  canvas = host.querySelector("#canvas");
  zoomBar = host.querySelector("#zoom");
  modeTabs = host.querySelector("#modeTabs");
  tabToolsSep = host.querySelector("#tabToolsSep");
  // **用語ページの中では出さない。** あちらは「その語の見方」のタブ列を自分で
  // 持っているので、辞書ぜんぶの見方をもう 1 列出すとタブが二重になる
  showTabs = tabs;
  modeTabs.hidden = !tabs;
  installTabKeys(modeTabs);
  timeAxisPick = host.querySelector("#timeAxis");
  timeRowsPick = host.querySelector("#timeRows");
  egoDepthPick = host.querySelector("#egoDepth");
  mapPick = host.querySelector("#mapPick");
  mapLayers = host.querySelector("#mapLayers");
  mapTimeBar = host.querySelector("#mapTimeBar");
  mapEdit = host.querySelector("#mapEdit");
  mapDialog = host.querySelector("#mapDialog");
  installMapDialog();
  detailNode = host.querySelector("#detail");
  notes = host.querySelector("#notes");
  legend = host.querySelector("#legend");
  statusNode = host.querySelector("#status");
  categorySelect = host.querySelector("#category");
  spoilerCheck = host.querySelector("#spoilers");
  readSoFarCheck = host.querySelector("#readSoFar");
  readSoFarBox = host.querySelector("#readSoFarBox");
  readSoFarCheck.checked = false;
  readSoFarCheck.addEventListener("change", () => {
    // 絞るだけならサーバへ行き直さない（同じデータを描き替えるだけ）
    if (lastGraph) paintGraph(lastGraph);
  });
  // **書き出しは ⋯ の中で、形式もその中で選ばせる**（→ CLAUDE.md の置き場所の表）。
  // 「⬇ 画像」と形式の select を横に並べていたときは、折り返しで別々の行に割れて
  // 何の形式なのか読めなかった。**PNG は貼るため、SVG は拡大と編集のため**なので、
  // どちらか一方にしないこと。保存するのは**いまの拡大率ではなく図の全体**
  // **地図はここから開く**（タブ列には無い —— あちらは「辞書ぜんぶの見方」の列で、
  // 地図は絵 1 枚ぶんの見方。→ `dict-tabs.js` の `OFF_TAB_MODES`）。
  // **タブから外しても入口は残すこと** —— 絵が 1 枚も無い辞書では用語ページの 🗺 も
  // 出ないので、ここを消すと**最初の 1 枚を入れる道が無くなる**（鶏と卵。
  // 絵が無ければ段の図に落ちるが、そのときも 🖼 絵 は出る）
  host.querySelector("#graphMenu").replaceWith(menuButton({
    ref: "graphMenu",
    title: "そのほかの操作",
    items: [
      {
        label: "🗺 地図",
        title: "座標を書いた語を絵の上に置く（絵 1 枚ぶんの見方）",
        onSelect: () => pickMode("map"),
      },
      { separator: true },
      { label: "⬇ PNG で保存", title: "貼る用", onSelect: () => saveImage("png") },
      { label: "⬇ SVG で保存", title: "拡大できる・あとから編集できる", onSelect: () => saveImage("svg") },
      { label: "🩺 点検", href: "/doctor", title: "辞書全体の壊れを点検する" },
    ],
  }));
  edgeDialog = host.querySelector("#edgeDialog");
  countNode = document.getElementById("count");   // topbar は覆いの外
  params = new URLSearchParams(search);
  currentScope = params.get("scope") || "";
  currentDoc = params.get("doc") || "";
  // **ビューアで文書を読んでいるときだけ出す**（`?doc=` が無ければ「全体の図」で、
  // どこまで読んだかは意味を持たない）。**覚えない** —— 開くたびに全体へ戻す
  // （伏せたまま開き直すと「関係が消えた」と読まれる）
  readSoFarBox.hidden = !(currentDoc && readingNow());
  readingNote = "";
  // 中心の図の初期値。用語ページの「この語を中心に」からはこれが付いてくる
  egoCenter = params.get("ref") || "";
  namedRef = egoCenter;
  embedded = embed;
  termByRef = new Map();
  lastGraph = null;
  // **戻せるのは開いている間だけ。** 覆いは何度でも開き直されるので、持ち越すと
  // 「いつのものか分からない形」を戻すボタンが出る（編集を覚えないのと同じ扱い）
  mapUndo = [];
  // **時点は開くたびに全部へ戻す。** 伏せたまま開き直すと「関係が消えた」と
  // 読まれる（「ここまで読んだぶん」と同じ約束）
  mapAt = null;
  mapTimeKey = "";

  // **listener は最初の await より前に付ける。** あとに回すと、その間の操作が
  // 黙って無視される（設定ダイアログと extract.js で 2 回踏んだ）
  mode = rememberedMode();
  // **URL が見せ方を名指ししていればそれが最優先。** 用語ページの「🗺 地図で見る」は
  // 語と見せ方の両方を指しているので、`?ref=` の「中心の図で開く」に負けさせない
  const wanted = params.get("mode") || "";
  // **タブに無い見せ方（地図）でも断りは出す。** 覚えているほうを押しのけたことと
  // 戻り道は、どちらの見せ方でも同じだけ要る（列には開いている間だけ出ている）
  modeFromUrl = ALL_MODES.includes(wanted) && wanted !== mode ? wanted : "";
  // **語を名指しで開かれたら中心の図で出す。** そうしないと、覚えている見せ方が
  // 段の図の人には「この語を中心に」を押しても何も起きない。**覚えているほうは
  // 書き換えない**（この 1 回だけの上書き）ので、次にふつうに開けば元に戻る
  centeredByUrl = Boolean(egoCenter) && mode !== "ego" && !ALL_MODES.includes(wanted);
  if (ALL_MODES.includes(wanted)) mode = wanted;
  else if (egoCenter) mode = "ego";
  syncModeOptions();
  // 絵も URL から指せる（用語ページはその語が置かれている絵を知っている）。
  // **こちらも覚えているほうは書き換えない**
  timeAxis = rememberedAxis();
  timeRows = rememberedRows();
  timeRowsPick.addEventListener("change", () => {
    timeRows = TIME_ROWS.includes(timeRowsPick.value) ? timeRowsPick.value : "node";
    try {
      localStorage.setItem(TIME_ROWS_KEY, timeRows);
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    // 並べるものを変えるだけならサーバへ行き直さない（同じデータの描き替え）
    if (lastGraph) paintGraph(lastGraph);
  });
  timeAxisPick.addEventListener("change", () => {
    timeAxis = TIME_AXES.includes(timeAxisPick.value) ? timeAxisPick.value : "read";
    try {
      localStorage.setItem(TIME_AXIS_KEY, timeAxis);
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    // 自分で選び直したなら、落としたときの断り書きはもう要らない
    axisFellBack = "";
    // 軸を変えるだけならサーバへ行き直さない（同じデータを並べ替えるだけ）
    if (lastGraph) paintGraph(lastGraph);
  });
  // 何つ先まで（中心の図）。**URL からも指せる** —— 用語ページから「直接の関係だけ」を
  // 見せたいときに、覚えているほうを書き換えずに 1 回だけ上書きできる
  egoDepth = params.has("depth") ? normalizeDepth(params.get("depth")) : rememberedDepth();
  egoDepthPick.addEventListener("change", () => {
    egoDepth = normalizeDepth(egoDepthPick.value);
    try {
      localStorage.setItem(EGO_DEPTH_KEY, String(egoDepth));
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    // 深さを変えるだけならサーバへ行き直さない（同じデータの描き替え）
    if (lastGraph) paintGraph(lastGraph);
  });
  mapName = params.get("map") || rememberedMap();
  mapHidden = rememberedHidden();
  mapNoLabels = rememberedNoLabels();
  mapFaces = rememberedFaces();
  mapPick.addEventListener("change", () => {
    mapName = mapPick.value;
    try {
      localStorage.setItem(MAP_KEY, mapName);
    } catch {
      /* 使えない環境でも選べること自体は動く */
    }
    // 絵を変えるだけならサーバへ行き直さない（同じデータを描き替えるだけ）
    if (lastGraph) paintGraph(lastGraph);
  });
  // タブは左右キーでも辿れるようにする（`role="tablist"` の作法）。
  // **押した先で描き直すのは `pickMode()` 1 か所**（矢印とクリックで道を分けない）
  categorySelect.addEventListener("change", refresh);
  spoilerCheck.addEventListener("change", refresh);
  installViewControls();
  installDetail();
  showDetail("");
  dlg("rank").replaceChildren(
    ...RANK_OPTIONS.map(([value, text]) => el("option", { value, text }))
  );
  dlg("save").addEventListener("click", onEdgeSave);
  dlg("remove").addEventListener("click", onEdgeRemove);
  for (const name of ["close", "cancel"]) {
    dlg(name).addEventListener("click", () => edgeDialog.close());
  }

  // 読み込みを待たずに書く。何の図なのかは最初から見えていないと意味が無い
  paintScope();
  paintEntryCount(countNode);
  await loadCategories();
  await refresh();
}
