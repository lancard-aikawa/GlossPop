// 交差しない見せ方（BioFabric）。**用語を横線、関係を縦線**にして、関係 1 本ごとに
// 独立した列を与える。列が重ならないので、**関係がいくら増えても線どうしは交わらない**。
//
// 段の図で交差が消せないのは、`rank` でノードが段に固定されているから
// （層に固定した交差最小化は隣り合う 2 層だけでも NP 困難）。ここは「辺のほうに
// 場所を与える」ことでその制約ごと外している。→ docs/design-notes.md
//
// **上下の意味は捨てていない。** 行の並びは段（`levelsOf`）が主で、上の段が上。
// 「関係が書かれていない語」を段の外にまとめるのも段の図と同じ（`splitLonely`）。
import {
  describeNode, describeRelation, estTextWidth, relationWords,
  svgEl as svg, svgVerticalText, verticalChars,
} from "./base.js";
import { levelsOf, seedOrder, splitLonely, wrapLonely } from "./graph-model.js";

const ROW_H = 26;            // 用語 1 語ぶんの高さ
const COL_W = 22;            // 関係 1 本ぶんの幅
const PAD = 16;
const LABEL_GAP = 14;
const STUB = 10;             // 名前と最初の列のあいだに出す横線
const NODE_FONT = 12;
const LONELY_GAP = 34;

//: 縦に書く一言の上限。長い一言をそのまま立てると見出しだけで画面が埋まる
//: （実例に 24 字のものがあった）。切ったことは「…」で分かる
const WORDS_MAX = 12;
//: 縦書きの字送り。base.js の既定と同じ値（高さの計算に要る）
const VERTICAL_LINE_H = 12.5;

function marker(id, className) {
  return svg("marker", {
    id,
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto-start-reverse",
  }, [svg("path", { d: "M0,0 L10,5 L0,10 z", class: className })]);
}

/**
 * 行の並び。**段が主、段の中は多く繋がっているものから幅優先**。
 *
 * 段を主にするのは、この図でも「上にあるものが上位」を保つため —— 見せ方を
 * 変えても言っていることが変わらないようにする。段の中を幅優先にするのは、
 * 繋がっている語を近い行に置くと縦線が短くなるから（BioFabric が階段状に
 * 見えるのはこのため。線が短いほど、どこからどこへ伸びているか追いやすい）。
 */
function orderRows(linked, edges) {
  const level = levelsOf(linked, edges);
  const rank = seedOrder(linked, edges);
  return [...linked].sort(
    (a, b) => (level.get(a.ref) || 0) - (level.get(b.ref) || 0)
      || (rank.get(a.ref) ?? 0) - (rank.get(b.ref) ?? 0)
  );
}

/**
 * 列の並び。**1 つの語から下へ伸びる関係をひとまとまりにする。**
 *
 * 行の順に見ていき、その語から「まだ出していない、自分より下の行」への関係を
 * 近い順に出す。こうすると 1 語ぶんの関係が連続した列に並び、名前の右から
 * 階段状に降りていく形になる。列をばらばらに並べると、同じ語の関係が図の
 * あちこちに散って「この語は誰と繋がっているのか」が読めなくなる。
 */
function orderColumns(edges, rowOf) {
  const out = [];
  const done = new Set();
  const byRow = new Map();
  edges.forEach((edge, i) => {
    const a = rowOf.get(edge.from);
    const b = rowOf.get(edge.to);
    if (a === undefined || b === undefined) return;
    const top = Math.min(a, b);
    if (!byRow.has(top)) byRow.set(top, []);
    byRow.get(top).push(i);
  });
  for (const top of [...byRow.keys()].sort((a, b) => a - b)) {
    const group = byRow.get(top);
    // 近い相手から。同じ相手が複数なら元の順（結果が揺れないように）
    group.sort((x, y) => {
      const far = (i) => Math.max(rowOf.get(edges[i].from), rowOf.get(edges[i].to));
      return far(x) - far(y) || x - y;
    });
    for (const i of group) {
      if (done.has(i)) continue;
      done.add(i);
      out.push(i);
    }
  }
  return out;
}

/**
 * 交差しない図を組み立てる。返すのは段の図と同じ ``{ root, box }``
 * （拡大縮小と移動は呼ぶ側が同じ仕組みで面倒を見る）。
 *
 * @param {object} graph  `/api/graph` の返り値
 * @param {function} onEdge 関係を押したときに呼ぶ（編集ダイアログ）
 */
export function buildFabric(graph, { onEdge } = {}) {
  const { nodes, edges } = graph;
  const { linked, lonely } = splitLonely(nodes, edges);
  const rows = orderRows(linked, edges);
  const rowOf = new Map(rows.map((n, i) => [n.ref, i]));
  const columns = orderColumns(edges, rowOf);
  const colOf = new Map(columns.map((edgeIndex, i) => [edgeIndex, i]));

  const labelW = Math.max(60, ...rows.map((n) => estTextWidth(n.term, NODE_FONT)), 0);
  // 一言は縦書き。高さは**字数**で決まる（幅ではない）
  const headH = Math.max(...edges.map((e) => verticalChars(relationWords(e), WORDS_MAX).length), 2)
    * VERTICAL_LINE_H + 12;
  const x0 = PAD + labelW + LABEL_GAP;
  const colX = (edgeIndex) => x0 + colOf.get(edgeIndex) * COL_W + COL_W / 2;
  const rowY = (i) => headH + PAD + i * ROW_H;

  const width = x0 + Math.max(columns.length, 1) * COL_W + PAD;
  const bodyBottom = rows.length ? rowY(rows.length - 1) + ROW_H / 2 : headH + PAD;
  // **関係の無い語は 1 語 1 行にしない。** 行にすると、関係を持たない語の数だけ
  // 図が縦に伸びる（31 語のうち 15 語が孤立していた実例では、関係のある部分より
  // 孤立語の並びのほうが背が高くなった）。段の図と同じく折り返して並べる
  const strip = wrapLonely(lonely, {
    width, top: bodyBottom, pad: PAD, rowHeight: ROW_H, gap: LONELY_GAP,
    widthOf: (node) => estTextWidth(node.term, NODE_FONT) + 28,
  });
  const height = strip.bottom + PAD;

  const root = svg("svg", {
    class: "rel-graph rel-fabric",
    width: "100%",
    height: "100%",
    viewBox: `0 0 ${Math.ceil(width)} ${Math.ceil(height)}`,
    role: "img",
    "aria-label": "用語の相関図（交差しない見せ方）",
  });
  root.append(svg("defs", {}, [marker("fab-arrow", "rel-arrowhead")]));

  // 語ごとに繋がっている列を集める。横線の長さと、乗せたときの強調に使う
  const touching = new Map(rows.map((n) => [n.ref, []]));

  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  for (const edgeIndex of columns) {
    const edge = edges[edgeIndex];
    const x = colX(edgeIndex);
    const ya = rowY(rowOf.get(edge.from));
    const yb = rowY(rowOf.get(edge.to));
    const cls = ["rel-edge", edge.missing ? "missing" : "", edge.reveal ? "reveal" : ""]
      .filter(Boolean).join(" ");
    const line = svg("line", {
      x1: x, y1: ya, x2: x, y2: yb,
      class: cls,
      "marker-end": "url(#fab-arrow)",
      // 相互なら両端に矢印。一方的なら向いている側だけ（段の図と同じ約束）
      "marker-start": edge.mutual ? "url(#fab-arrow)" : null,
    });
    const detail = describeRelation(edge, {
      from: rows[rowOf.get(edge.from)]?.term, to: rows[rowOf.get(edge.to)]?.term,
    });
    const group = svg("g", {
      class: "rel-edge-group",
      tabindex: "0",
      role: "button",
      "aria-label": `関係を直す: ${detail}`,
      // 図の下の枠に出す文。**縦書きは 12 字で切るので、全文はここでしか読めない**
      "data-detail": detail,
    }, [
      // 縦線は細くて押せない。透明な太い線を重ねて当たり判定にする（段の図と同じ）
      svg("line", { x1: x, y1: ya, x2: x, y2: yb, class: "rel-edge-hit" }),
      line,
      svg("circle", { cx: x, cy: ya, r: 3, class: "fab-end" }),
      svg("circle", { cx: x, cy: yb, r: 3, class: "fab-end" }),
    ]);
    group.append(svg("title", { text: `${detail}（押すと直せます）` }));

    const words = relationWords(edge);
    // 一言は列の上に縦書き。横に書くと 22px の列幅に収まらない
    const text = words
      ? svgVerticalText(words, x, headH + PAD - 14,
        { max: WORDS_MAX, className: "rel-edge-label" })
      : null;
    // 一言に乗せたときも下の枠に出す（切れている全文はそこでしか読めない）
    text?.setAttribute("data-detail", detail);
    lines.append(group);
    if (text) labels.append(text);

    // 線と一言は一緒に光らせる（段の図と同じ理由 —— 別々の層に居る）
    const light = (on) => {
      group.classList.toggle("hot", on);
      text?.classList.toggle("hot", on);
    };
    const open = () => onEdge?.(edge);
    for (const node of [group, text]) {
      if (!node) continue;
      node.addEventListener("pointerenter", () => light(true));
      node.addEventListener("pointerleave", () => light(false));
      node.addEventListener("click", open);
    }
    group.addEventListener("focus", () => light(true));
    group.addEventListener("blur", () => light(false));
    group.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      open();
    });

    for (const ref of [edge.from, edge.to]) {
      touching.get(ref)?.push({ group, text, x, other: ref === edge.from ? edge.to : edge.from });
    }
  }
  root.append(lines, labels);

  const nodeGroups = new Map();
  rows.forEach((node, i) => {
    const y = rowY(i);
    const spots = touching.get(node.ref) || [];
    const right = spots.length ? Math.max(...spots.map((s) => s.x)) : x0;
    const cls = ["rel-node", node.missing ? "missing" : "", node.outside ? "outside" : ""]
      .filter(Boolean).join(" ");
    const group = svg("g", { class: cls, "data-detail": describeNode(node) }, [
      svg("line", { x1: x0 - STUB, y1: y, x2: right, y2: y, class: "fab-line" }),
    ]);
    const link = svg("a", { href: node.url }, [
      svg("text", {
        x: x0 - STUB - 6,
        y,
        "text-anchor": "end",
        "dominant-baseline": "central",
        text: node.term,
      }),
      svg("title", {
        text: node.missing
          ? `${node.term} — 未登録（押すと辞書で探せます）`
          : [node.path_label, node.summary].filter(Boolean).join(" — "),
      }),
    ]);
    group.append(link);
    root.append(group);
    nodeGroups.set(node.ref, group);
  });

  // 関係の無い語との境目。**行ではない**と分かるように区切る（段の図と同じ）
  if (lonely.length) {
    root.append(svg("g", { class: "rel-lonely-rule" }, [
      svg("line", {
        x1: PAD, y1: strip.ruleY, x2: Math.max(width - PAD, PAD + 40), y2: strip.ruleY,
      }),
      svg("text", {
        x: PAD,
        y: strip.ruleY - 6,
        class: "rel-lonely-caption",
        text: `関係が書かれていない語（${lonely.length}）`,
      }),
    ]));
    for (const { node, x, y } of strip.cells) {
      root.append(svg("g", { class: "rel-node", "data-detail": describeNode(node) }, [
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
  return {
    root,
    box: { x: 0, y: 0, w: width, h: height },
    lonely: lonely.length,
  };
}

/** 1 つの語に乗せている間、その語の関係だけを濃く出す（段の図と同じ作法）。 */
function installFocus(root, nodeGroups, touching) {
  const light = (ref, on) => {
    root.classList.toggle("focusing", on);
    if (!on) {
      for (const node of root.querySelectorAll(".lit")) node.classList.remove("lit");
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
    group.addEventListener("focusin", () => light(ref, true));
    group.addEventListener("focusout", () => light(ref, false));
  }
}
