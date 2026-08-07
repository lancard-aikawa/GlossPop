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
  api, describeNode, describeRelation, el, estTextWidth, paintEntryCount,
  RANK_OPTIONS, relationWords, setStatus, svgEl,
} from "./base.js";
import { levelsOf, seedOrder, splitLonely } from "./graph-model.js";
import { buildFabric } from "./fabric.js";
import { buildMatrix } from "./matrix.js";
import { buildTimeline } from "./timeline.js";
import { buildEgo } from "./ego.js";
import { buildMap } from "./map.js";
import { encodePath } from "./editor.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない。2 つに割ると、
//: 片方だけ直したときに「ページでは出るのに重ねると出ない」になる）
const TEMPLATE = `
<div class="toolbar graph-toolbar">
  <!-- **何を出している図なのかを必ず書く。** 書かないと、辞書全体の図を
       「開いている文書の図」だと思われる（その取り違えが元で下書きを
       ここから外した。→ docs/design-notes.md） -->
  <span class="hint" id="scopeNote"></span>
  <a class="btn" id="scopeAll" href="/graph" hidden>辞書全体を出す</a>
  <!-- **見せ方は足すもので、置き換えではない。** 同じ辞書を別の読み方で出すだけ
       なので、段の図の約束（上下は段で表す・伏せた本数を返す）は両方で守る -->
  <select id="mode" class="auto-width" aria-label="見せ方">
    <option value="layered">段の図</option>
    <option value="fabric">交差しない図</option>
    <option value="matrix">行列</option>
    <!-- 1 語を中心にした図。**規模に依らない**唯一の見せ方（→ ego.js） -->
    <option value="ego">中心の図（1 語）</option>
    <!-- 時系列は「読むもの」が決まっていないと定義できないので、辞書全体の図では
         選べない（文書を絞っているときだけ。→ timeline.js） -->
    <option value="timeline">時系列（文書を開いているとき）</option>
    <!-- 地図。**座標を書いた語があるときだけ**出せる（分類ではなく、書いてある
         ものだけが出る。→ map.js） -->
    <option value="map">地図（座標のある語）</option>
  </select>
  <!-- どの絵を出すか。**辞書に数枚ある**ので選べないと「ほかに 〇〇 があります」と
       書いておきながら行けない。地図のとき、絵が 2 枚以上あるときだけ出す -->
  <select id="mapPick" class="auto-width" aria-label="地図" hidden></select>
  <!-- **落ちているときも出す。** 絵が 1 枚も無いと段の図に落ちるので、ここを
       隠すと「最初の 1 枚を入れる」道が無くなる（鶏と卵になる） -->
  <button type="button" class="btn" id="mapEdit" hidden title="地図の絵を入れる / 消す">🖼 絵</button>
  <select id="category" class="auto-width" aria-label="カテゴリ"></select>
  <label class="check">
    <input type="checkbox" id="spoilers">
    <span>判明位置つきの関係も出す</span>
  </label>
  <!-- **ここに「関係を下書き」を戻さないこと。** この図は辞書全体を出すのに、
       下書きは開いているフォルダの本文を読む。並べると「図に出ている語について
       探す」と読まれるが、実際に読むのはビューアで開いているものとも図とも
       一致しない範囲だった（→ docs/design-notes.md） -->
  <a class="btn" href="/doctor" title="辞書全体の壊れを点検する">🩺 点検</a>
  <span class="spacer"></span>
  <span class="status" id="status"></span>
</div>
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
let mapPick, mapLayers, mapEdit, mapDialog;
let detailNode;
let modeSelect;

//: 見せ方。layered = 段の図（既定） / fabric = 交差しない図 / matrix = 行列 /
//: ego = 1 語を中心にした図 / timeline = 時系列（文書を開いているときだけ）/
//: map = 地図（座標を書いた語があるときだけ）。
//: **覚えておく** —— 覆いは何度でも開き直されるので、毎回選び直させない
const MODE_KEY = "glosspop.graphMode";
const MODES = ["layered", "fabric", "matrix", "ego", "timeline", "map"];
//: 読むものが決まっているときしか出せない見せ方。時系列は「その文書のどこで
//: 読めるようになるか」を軸にするので、辞書全体では定義できない（`?doc=` と同じ話）
const DOC_ONLY_MODES = ["timeline"];
let mode = "layered";

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
  const build = {
    fabric: buildFabric, matrix: buildMatrix, timeline: buildTimeline, ego: buildEgo,
    map: mapFellBack ? null : buildMap,
  }[mode];
  const drawn = build
    ? build(graph, {
      onEdge: openEdgeEditor,
      center: egoCenter,
      onCenter: moveCenter,
      mapName,
      hidden: mapHidden.get(mapName),
      editing: mapEditing,
      placing: mapPlacing,
      onMove: saveMapShape,
      onPlace: (ref, points) => {
        mapPlacing = "";
        saveMapShape(ref, "point", points);
      },
      labels: !mapNoLabels.has(mapName),
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
  return {
    lonely: drawn.lonely,
    linked: nodes.length - drawn.lonely,
    tucked: drawn.tucked || 0,
    // 見せ方が「出していないもの」を数えていれば、そのまま凡例へ通す
    // （**ここで落とすと黙って欠けた図になる**。実際に一度落とした）
    note: drawn.note || "",
    // 地図が「この絵に置ける語」を返していれば、そのままチェックの一覧へ通す
    // （**ここで落とすと一覧が空になり、外したものを戻せなくなる**。実際に落とした）
    items: drawn.items || [],
    pending: drawn.pending || [],
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
  if (centeredByUrl && mode === "ego") {
    // 覚えている見せ方を押しのけたことは書く（次にふつうに開けば元に戻る）
    lines.push(
      "語が名指しされているので、中心の図で開いています"
      + "（見せ方は上で戻せます。覚えているほうは変えていません）。"
    );
  }
  if (modeFellBack) {
    // 覚えていた見せ方を黙って別のものに差し替えない
    lines.push(
      "時系列は文書を開いているときだけ出せるので、いまは段の図にしています" +
      "（ビューアの「🕸 この文書の相関図」から開くと出せます）。"
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

/** 覚えている絵。**読めなくても困らない** —— いちばん多く点が乗るものに落ちる。 */
function rememberedMap() {
  try {
    return localStorage.getItem(MAP_KEY) || "";
  } catch {
    return "";
  }
}

/** 覚えている見せ方。読めない値なら段の図へ落ちる（起動できなくならないこと）。 */
function rememberedMode() {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    return MODES.includes(saved) ? saved : "layered";
  } catch {
    return "layered";
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

//: 地図を編集中か（丸を掴んで動かせる）。**覚えない** —— 開くたびに閲覧へ戻す。
//: 見せ方や絵と違って、うっかり動かすほうの害が大きい
let mapEditing = false;
//: 「次に絵を押したらここへ置く」語。1 回置いたら空に戻す
let mapPlacing = "";

//: URL で語を名指しされたので、覚えている見せ方を押しのけて中心の図で開いたか。
//: これも黙ってやらない（同じ理由）
let centeredByUrl = false;

/**
 * いまの範囲で選べる見せ方に揃える。
 *
 * 時系列は `?doc=` のときしか定義できないので、辞書全体の図では選ばせない。
 * **覚えている選択は書き換えない** —— 文書を開いて戻ってきたら、また時系列で
 * 出したい（一度だけ辞書全体を見たせいで設定が消えるのは驚く）。
 */
function syncModeOptions() {
  for (const value of DOC_ONLY_MODES) {
    const option = modeSelect.querySelector(`option[value="${value}"]`);
    if (option) option.disabled = !currentDoc;
  }
  modeFellBack = DOC_ONLY_MODES.includes(mode) && !currentDoc;
  if (modeFellBack) mode = "layered";
  modeSelect.value = mode;
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
async function saveMapShape(ref, kind, points) {
  try {
    await api(`/api/map-shape/${encodePath(ref)}`, {
      method: "PUT",
      body: { kind, points },
    });
    setStatus(statusNode, "地図の位置を保存しました");
    await refresh();
  } catch (err) {
    setStatus(statusNode, err.message, "error");
  }
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
    mapPlacing = "";
    if (lastGraph) paintGraph(lastGraph);
  });
  const parts = [
    el("label", {
      class: "check",
      "data-ref": "mapEdit",
      title: "丸を掴んで位置を動かせるようにする（離すと保存されます）",
    }, [editBox, el("span", { text: "置く" })]),
    el("label", {
      class: "check",
      "data-ref": "mapNames",
      title: "絵に地名が入っているときは外す（乗せれば名前は出ます）",
    }, [nameBox, el("span", { text: "名前を出す" })]),
    el("span", { class: "hint", text: "地図に出すもの:" }),
  ];
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
      parts.push(el("label", { class: "check", "data-ref": "mapItem" },
        [box, el("span", { text: item.term })]));
    }
  }
  // **まだ置いていない語**（絵の名前だけ書いてある）。押してから絵を押すと置く。
  // 分類していない —— 「この絵に置きたい」と書いてあるものだけが並ぶ
  const pending = drawn.pending || [];
  if (mapEditing && pending.length) {
    parts.push(el("span", { class: "hint", text: "まだ置いていない:" }));
    for (const item of pending) {
      parts.push(el("button", {
        type: "button",
        class: mapPlacing === item.ref ? "chip primary" : "chip",
        "data-ref": "mapPending",
        text: item.term,
        title: "押してから絵の上を押すと、そこへ置きます",
        onclick: () => {
          mapPlacing = mapPlacing === item.ref ? "" : item.ref;
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

function paintGraph(graph) {
  lastGraph = graph;
  showDetail("");
  const drawn = draw(graph);
  paintMapOptions(graph);
  paintMapLayers(drawn);
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
    ego:
      "真ん中の語から 2 つ先までを環に並べています。"
      + "内側の環が 1 つ先、外側が 2 つ先。まわりの語を押すとそこが中心に移り、"
      + "真ん中の語を押すと辞書ページが開きます。"
      + "上下の関係は置き場所で表しています（上にあるものが上位、対等は左右）。",
    // 「いつ」が見えるのがこの見せ方の役目。位置は毎回その場で計算していて
    // 保存はしていない、と書いておかないと「編集したらずれる」と読まれる
    timeline:
      "上から順に、この文書を読み進めると関係が読めるようになる順です。"
      + "左の見出しは、両方の語が出そろう位置（章・ページ・行）。"
      + "位置は開くたびに本文から数えていて保存はしないので、本文を直せば次に開いたときに追いつきます。"
      + "「判明: …」は人が書いた判明位置で、並べ替えには使っていません。",
    // 「どこ」が見えるのがこの見せ方の役目。**分類していない**と書いておかないと、
    // 「地名なのに出てこない」を機械の取りこぼしだと読まれる
    map:
      "座標を書いた語を絵の上に置いています。"
      + "どれが地名かは決めていません —— 座標を書いた語が出るだけなので、"
      + "出したい語には map と pin を書いてください。"
      + "線は、両端がこの絵に置かれている関係だけです。",
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
export async function mount(host, { search = "", embed = false } = {}) {
  host.innerHTML = TEMPLATE;
  canvas = host.querySelector("#canvas");
  zoomBar = host.querySelector("#zoom");
  modeSelect = host.querySelector("#mode");
  mapPick = host.querySelector("#mapPick");
  mapLayers = host.querySelector("#mapLayers");
  mapEdit = host.querySelector("#mapEdit");
  mapDialog = host.querySelector("#mapDialog");
  installMapDialog();
  detailNode = host.querySelector("#detail");
  notes = host.querySelector("#notes");
  legend = host.querySelector("#legend");
  statusNode = host.querySelector("#status");
  categorySelect = host.querySelector("#category");
  spoilerCheck = host.querySelector("#spoilers");
  edgeDialog = host.querySelector("#edgeDialog");
  countNode = document.getElementById("count");   // topbar は覆いの外
  params = new URLSearchParams(search);
  currentScope = params.get("scope") || "";
  currentDoc = params.get("doc") || "";
  // 中心の図の初期値。用語ページの「この語を中心に」からはこれが付いてくる
  egoCenter = params.get("ref") || "";
  embedded = embed;
  termByRef = new Map();
  lastGraph = null;

  // **listener は最初の await より前に付ける。** あとに回すと、その間の操作が
  // 黙って無視される（設定ダイアログと extract.js で 2 回踏んだ）
  mode = rememberedMode();
  // **語を名指しで開かれたら中心の図で出す。** そうしないと、覚えている見せ方が
  // 段の図の人には「この語を中心に」を押しても何も起きない。**覚えているほうは
  // 書き換えない**（この 1 回だけの上書き）ので、次にふつうに開けば元に戻る
  centeredByUrl = Boolean(egoCenter) && mode !== "ego";
  if (egoCenter) mode = "ego";
  syncModeOptions();
  mapName = rememberedMap();
  mapHidden = rememberedHidden();
  mapNoLabels = rememberedNoLabels();
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
  modeSelect.addEventListener("change", () => {
    mode = MODES.includes(modeSelect.value) ? modeSelect.value : "layered";
    // 自分で選び直したなら、落とした / 押しのけたときの断り書きはもう要らない
    modeFellBack = false;
    mapFellBack = false;
    centeredByUrl = false;
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* 使えない環境でも選べること自体は動く */
    }
    // 見せ方を変えるだけならサーバへ行き直さない（同じデータを描き替えるだけ）
    if (lastGraph) paintGraph(lastGraph);
  });
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
