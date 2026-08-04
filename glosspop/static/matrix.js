// 行列の見せ方（NodeTrix の考え方）。用語を行と列に並べ、**関係をマスで表す**。
// 線を 1 本も引かないので、交差という概念が無い。
//
// **この見せ方だけができること: 「無い」が見える。** 段の図も交差しない図も、
// 書かれた関係しか描かない —— 「まだ書いていない組」は絵に出ないので数えられない。
// 行列は空きマスとして残るので、**関係の抜けを探す**のに使える。
//
// カテゴリの切れ目に線を入れる（NodeTrix の「かたまりごとの行列」に当たる）。
// 対角を挟んで左右対称なら相互、片側だけなら一方的。
import {
  estTextWidth, svgEl as svg, svgVerticalText, verticalChars,
} from "./base.js";
import { levelsOf, seedOrder, splitLonely, wrapLonely } from "./graph-model.js";

const CELL = 22;             // マス 1 つの大きさ
const PAD = 16;
const LABEL_GAP = 10;
const NODE_FONT = 12;
const LONELY_GAP = 34;
const ROW_H = 26;            // 関係の無い語を並べるときの行の高さ

//: 見出しに立てる字数の上限。用語名は一言より短いので少し長めに取れる
const NAME_MAX = 10;
const LINE_H = 12.5;

//: カテゴリの鍵。**名前だけで束ねない** —— 同名のカテゴリが全体とフォルダの
//: 両方にありうる（`/` も `<>` もカテゴリ名では弾かれるので衝突しない）
const groupKey = (node) => `${node.scope || ""}<>${node.category || ""}`;

function edgeTitle(edge, fromTerm, toTerm) {
  const bits = [`${fromTerm} → ${toTerm}`, edge.mutual ? "相互" : "一方的"];
  if (edge.label) bits.push(edge.label);
  if (edge.back && edge.back !== edge.label) bits.push(`逆: ${edge.back}`);
  if (edge.rank) bits.push(`相手が${edge.rank}`);
  if (edge.reveal) bits.push(`判明: ${edge.reveal}`);
  return bits.join(" / ");
}

/**
 * 行と列の並び。**カテゴリでまとめ、その中は段（上下）が主。**
 *
 * カテゴリでまとめるのは、関係が同じカテゴリの中に固まるから —— まとめると
 * 埋まったマスが対角のまわりに寄り、離れたマスが「カテゴリをまたぐ関係」に
 * なる。段を次に見るのは、他の見せ方と並びを揃えて「上にあるものが上位」を
 * 保つため（見せ方を変えても言っていることを変えない）。
 */
function orderNodes(linked, edges) {
  const level = levelsOf(linked, edges);
  const rank = seedOrder(linked, edges);
  // カテゴリの並びはサーバが返した順（マスターの並び）を保つ
  const groupAt = new Map();
  linked.forEach((n) => {
    if (!groupAt.has(groupKey(n))) groupAt.set(groupKey(n), groupAt.size);
  });
  return [...linked].sort(
    (a, b) => groupAt.get(groupKey(a)) - groupAt.get(groupKey(b))
      || (level.get(a.ref) || 0) - (level.get(b.ref) || 0)
      || (rank.get(a.ref) ?? 0) - (rank.get(b.ref) ?? 0)
  );
}

/**
 * 行列を組み立てる。返すのは他の見せ方と同じ ``{ root, box }``。
 *
 * @param {object} graph  `/api/graph` の返り値
 * @param {function} onEdge 関係を押したときに呼ぶ（編集ダイアログ）
 */
export function buildMatrix(graph, { onEdge } = {}) {
  const { nodes, edges } = graph;
  // 関係の無い語は行にも列にもしない。**行と列の両方が空になる**ので、
  // 語の数だけ縦にも横にも空きが伸びる（他の見せ方と同じく下にまとめる）
  const { linked, lonely } = splitLonely(nodes, edges);
  const order = orderNodes(linked, edges);
  const at = new Map(order.map((n, i) => [n.ref, i]));
  const n = order.length;

  const labelW = Math.max(60, ...order.map((x) => estTextWidth(x.term, NODE_FONT)), 0);
  const headH = Math.max(
    ...order.map((x) => verticalChars(x.term, NAME_MAX).length), 2
  ) * LINE_H + 10;
  const x0 = PAD + labelW + LABEL_GAP;
  const y0 = PAD + headH + LABEL_GAP;
  const cellX = (j) => x0 + j * CELL;
  const cellY = (i) => y0 + i * CELL;

  const gridW = n * CELL;
  const width = Math.max(x0 + gridW + PAD, 320);
  const strip = wrapLonely(lonely, {
    width, top: y0 + gridW, pad: PAD, rowHeight: ROW_H, gap: LONELY_GAP,
    widthOf: (node) => estTextWidth(node.term, NODE_FONT) + 28,
  });
  const height = strip.bottom + PAD;

  const root = svg("svg", {
    class: "rel-graph rel-matrix",
    width: "100%",
    height: "100%",
    viewBox: `0 0 ${Math.ceil(width)} ${Math.ceil(height)}`,
    role: "img",
    "aria-label": "用語の相関図（行列の見せ方）",
  });

  // 格子。**空きマスも描く** —— 「関係が無い」を読ませるのがこの見せ方の役目で、
  // 埋まったマスだけ置くと、どこが空いているのか数えられない
  const grid = svg("g", { class: "mx-grid" });
  for (let i = 0; i <= n; i++) {
    grid.append(svg("line", { x1: x0, y1: cellY(i), x2: x0 + gridW, y2: cellY(i) }));
    grid.append(svg("line", { x1: cellX(i), y1: y0, x2: cellX(i), y2: y0 + gridW }));
  }
  root.append(grid);

  // 対角は「自分自身」。関係は書けないので、空きと見分けられるようにしておく
  const diag = svg("g", { class: "mx-diagonal" });
  for (let i = 0; i < n; i++) {
    diag.append(svg("rect", { x: cellX(i), y: cellY(i), width: CELL, height: CELL }));
  }
  root.append(diag);

  // カテゴリの切れ目。ここが NodeTrix の「かたまり」に当たる
  const splits = svg("g", { class: "mx-split" });
  order.forEach((node, i) => {
    if (!i || groupKey(node) === groupKey(order[i - 1])) return;
    splits.append(svg("line", { x1: PAD, y1: cellY(i), x2: x0 + gridW, y2: cellY(i) }));
    splits.append(svg("line", { x1: cellX(i), y1: PAD, x2: cellX(i), y2: y0 + gridW }));
  });
  root.append(splits);

  // 語ごとに、その行と列に居るマス。乗せたときの強調に使う
  const touching = new Map(order.map((x) => [x.ref, []]));
  const cells = svg("g", { class: "mx-cells" });
  for (const edge of edges) {
    const i = at.get(edge.from);
    const j = at.get(edge.to);
    if (i === undefined || j === undefined || i === j) continue;
    const term = (ref) => order[at.get(ref)].term;
    const cls = ["mx-cell", edge.missing ? "missing" : "", edge.reveal ? "reveal" : ""]
      .filter(Boolean).join(" ");
    // **相互なら対角の両側を埋める。** 関係はファイルには片側にしか書かれないが、
    // 行列で片側だけ埋めると一方的に見える —— 「対角を挟んで両側が埋まっていれば
    // 相互」と画面に書いてある以上、そちらに合わせる。2 つのマスは 1 つの部品
    // （同じ関係なので、どちらを押しても同じ編集が開く）
    const spots = edge.mutual ? [[i, j], [j, i]] : [[i, j]];
    const group = svg("g", {
      class: `rel-edge-group ${cls}`,
      tabindex: "0",
      role: "button",
      "aria-label": `関係を直す: ${edgeTitle(edge, term(edge.from), term(edge.to))}`,
    }, spots.map(([r, c]) => svg("rect", {
      x: cellX(c) + 3, y: cellY(r) + 3, width: CELL - 6, height: CELL - 6, rx: 3,
    })));
    group.append(svg("title", {
      text: `${edgeTitle(edge, term(edge.from), term(edge.to))}（押すと直せます）`,
    }));
    const open = () => onEdge?.(edge);
    group.addEventListener("click", open);
    group.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      open();
    });
    cells.append(group);
    touching.get(edge.from)?.push({ group, other: edge.to });
    touching.get(edge.to)?.push({ group, other: edge.from });
  }
  // 見出し。行は左に横書き、列は上に縦書き（横書きだと 22px の列幅に入らない）。
  // **行と列そのものを掴めるようにする** —— 行列は目で横へたどるのが難しく、
  // 名前の上だけが反応するとマスと名前を何度も往復することになる。
  // **マスより先に置く**（透明な帯が上に乗ると、マスが押せなくなる）
  const nodeGroups = new Map();
  order.forEach((node, i) => {
    const group = svg("g", { class: node.missing ? "rel-node missing" : "rel-node" }, [
      svg("rect", {
        class: "mx-band",
        x: PAD, y: cellY(i), width: x0 + gridW - PAD, height: CELL,
      }),
      svg("rect", {
        class: "mx-band",
        x: cellX(i), y: PAD, width: CELL, height: y0 + gridW - PAD,
      }),
    ]);
    const head = svg("a", { href: node.url }, [
      svg("text", {
        x: x0 - LABEL_GAP, y: cellY(i) + CELL / 2,
        "text-anchor": "end", "dominant-baseline": "central", text: node.term,
      }),
      svgVerticalText(node.term, cellX(i) + CELL / 2, y0 - LABEL_GAP - 4, {
        max: NAME_MAX,
        lineHeight: LINE_H,
      }),
      svg("title", {
        text: node.missing
          ? `${node.term} — 未登録（押すと辞書で探せます）`
          : [node.path_label, node.summary].filter(Boolean).join(" — "),
      }),
    ]);
    group.append(head);
    root.append(group);
    nodeGroups.set(node.ref, group);
  });
  root.append(cells);

  if (lonely.length) {
    root.append(svg("g", { class: "rel-lonely-rule" }, [
      svg("line", {
        x1: PAD, y1: strip.ruleY, x2: Math.max(width - PAD, PAD + 40), y2: strip.ruleY,
      }),
      svg("text", {
        x: PAD, y: strip.ruleY - 6, class: "rel-lonely-caption",
        text: `関係が書かれていない語（${lonely.length}）`,
      }),
    ]));
    for (const { node, x, y } of strip.cells) {
      root.append(svg("g", { class: "rel-node" }, [
        svg("a", { href: node.url }, [
          svg("text", {
            x, y, "text-anchor": "middle", "dominant-baseline": "central", text: node.term,
          }),
          svg("title", {
            text: [node.path_label, node.summary].filter(Boolean).join(" — "),
          }),
        ]),
      ]));
    }
  }

  installFocus(root, nodeGroups, touching);
  return { root, box: { x: 0, y: 0, w: width, h: height }, lonely: lonely.length };
}


/**
 * 1 つの語に乗せている間、その語の**行と列**だけを濃く出す（他の見せ方と同じ作法）。
 *
 * 行列では「行をたどって列を読む」ので、十字に残すのがそのまま読み方になる。
 */
function installFocus(root, nodeGroups, touching) {
  const light = (ref, on) => {
    root.classList.toggle("focusing", on);
    if (!on) {
      for (const node of root.querySelectorAll(".lit")) node.classList.remove("lit", "here");
      return;
    }
    // 十字に残すのは**乗せた語だけ**。相手の行と列まで塗ると、盤面の半分が
    // 色で埋まって「どこを見ればいいのか」が消える（相手は名前を明るくするだけ）
    nodeGroups.get(ref)?.classList.add("lit", "here");
    for (const { group, other } of touching.get(ref) || []) {
      group.classList.add("lit");
      nodeGroups.get(other)?.classList.add("lit");
    }
  };
  for (const [ref, group] of nodeGroups) {
    group.addEventListener("pointerenter", () => light(ref, true));
    group.addEventListener("pointerleave", () => light(ref, false));
    group.addEventListener("focusin", () => light(ref, true));
    group.addEventListener("focusout", () => light(ref, false));
  }
}
