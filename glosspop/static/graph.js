// 相関図。サーバはノードと辺だけを返し、**配置はここでやる**。
//
// 力学モデル (力の釣り合いを反復して解く) は使わない。カテゴリという階層が
// すでにあるうえ、`rank`（上下）で層が決まるので、決定的に置けば足りる。
// 乱数も収束待ちも無いぶん、同じ辞書なら毎回同じ絵になる。
import { api, el, paintEntryCount, setStatus } from "./base.js";
import { openRelationsDialog } from "./relations-draft.js";

const canvas = document.getElementById("canvas");
const notes = document.getElementById("notes");
const legend = document.getElementById("legend");
const statusNode = document.getElementById("status");
const countNode = document.getElementById("count");
const categorySelect = document.getElementById("category");
const spoilerCheck = document.getElementById("spoilers");
const draftButton = document.getElementById("draft");

const SVG_NS = "http://www.w3.org/2000/svg";

// ノードの箱。日本語は 1 文字がほぼ全角なので、文字数から幅を見積もる
const CHAR_W = 15;
const NODE_H = 40;
const NODE_MIN_W = 72;
const NODE_MAX_W = 220;
const GAP_X = 34;
const GAP_Y = 110;
const PAD = 40;

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function nodeWidth(term) {
  const chars = [...String(term || "")].length;
  return Math.max(NODE_MIN_W, Math.min(NODE_MAX_W, chars * CHAR_W + 24));
}

// --------------------------------------------------------------------------- //
// 配置
// --------------------------------------------------------------------------- //

/**
 * `rank` から層を決める。`上` は「相手が自分より上」なので、辺の向きに関係なく
 * 上下の制約だけを見る。
 *
 * **`対等` は「同じ段」という制約**なので、先にまとめてから上下を解く。
 * これをやらないと、A と B が対等でも B だけが誰かの下に引っ張られて段が割れ、
 * 「上下は段で表す」という説明と食い違う（実際にそうなった）。
 * 閉路や矛盾した指定があっても回数で打ち切るので止まらなくはならない。
 */
function levelsOf(nodes, edges) {
  const parent = new Map(nodes.map((n) => [n.ref, n.ref]));
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  };
  for (const e of edges) {
    if (e.rank !== "対等" || !parent.has(e.from) || !parent.has(e.to)) continue;
    const a = find(e.from);
    const b = find(e.to);
    if (a !== b) parent.set(a, b);
  }

  const level = new Map([...parent.keys()].map((ref) => [find(ref), 0]));
  const constraints = [];
  for (const e of edges) {
    if (!parent.has(e.from) || !parent.has(e.to)) continue;
    const a = find(e.from);
    const b = find(e.to);
    if (a === b) continue;                                 // 対等でまとめた者どうし
    if (e.rank === "上") constraints.push([b, a]);         // to が上
    else if (e.rank === "下") constraints.push([a, b]);    // from が上
  }
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const [up, down] of constraints) {
      if (level.get(down) <= level.get(up)) {
        level.set(down, level.get(up) + 1);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return new Map(nodes.map((n) => [n.ref, level.get(find(n.ref)) || 0]));
}

/** 隣接ノードの平均位置で並べ替える（線の交差をひととおり減らす）。 */
function orderRow(row, placed, neighbors) {
  return row
    .map((ref, i) => {
      const near = (neighbors.get(ref) || [])
        .map((other) => placed.get(other))
        .filter((x) => x !== undefined);
      const key = near.length ? near.reduce((a, b) => a + b, 0) / near.length : i;
      return { ref, key, i };
    })
    .sort((a, b) => a.key - b.key || a.i - b.i)
    .map((x) => x.ref);
}

/** 層が 1 枚しかないときの円配置。横一列に並べると辺が全部重なる。 */
function circleLayout(nodes) {
  const n = nodes.length;
  const radius = Math.max(140, (n * (NODE_MIN_W + GAP_X)) / (2 * Math.PI));
  const cx = radius + NODE_MAX_W / 2 + PAD;
  const cy = radius + NODE_H + PAD;
  const pos = new Map();
  nodes.forEach((node, i) => {
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

function layeredLayout(nodes, edges, level) {
  const rows = new Map();
  for (const node of nodes) {
    const l = level.get(node.ref) || 0;
    if (!rows.has(l)) rows.set(l, []);
    rows.get(l).push(node.ref);
  }
  const neighbors = new Map(nodes.map((n) => [n.ref, []]));
  for (const e of edges) {
    neighbors.get(e.from)?.push(e.to);
    neighbors.get(e.to)?.push(e.from);
  }
  const byRef = new Map(nodes.map((n) => [n.ref, n]));

  const pos = new Map();
  const placed = new Map();     // ref -> x (直前の層の位置を次の層の並べ替えに使う)
  let width = 0;
  const sorted = [...rows.keys()].sort((a, b) => a - b);
  sorted.forEach((l, rowIndex) => {
    const row = orderRow(rows.get(l), placed, neighbors);
    const widths = row.map((ref) => nodeWidth(byRef.get(ref).term));
    const total = widths.reduce((a, b) => a + b, 0) + GAP_X * (row.length - 1);
    let x = PAD;
    row.forEach((ref, i) => {
      const w = widths[i];
      const cx = x + w / 2;
      pos.set(ref, { x: cx, y: PAD + NODE_H / 2 + rowIndex * GAP_Y, w, h: NODE_H });
      placed.set(ref, cx);
      x += w + GAP_X;
    });
    width = Math.max(width, total + PAD * 2);
  });
  return { pos, width, height: PAD * 2 + NODE_H + (sorted.length - 1) * GAP_Y };
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

//: ラベルを線上のどこに置くか。1 つのノードに何本も集まると中点が同じ帯に並んで
//: 文字が重なるので、辺ごとにずらす（実際に読めなくなった）
const LABEL_SPOTS = [0.5, 0.34, 0.66, 0.42, 0.58];

/**
 * @param {number} index    全体での通し番号。ラベルを線上でずらすのに使う
 * @param {number} parallel 同じ 2 ノードを結ぶ何本目か。0 なら 1 本目。
 *   同じ組を複数の関係が結ぶことがある（「親友」と「実は〜」など）。ずらさないと
 *   線もラベルも完全に重なって、2 本あることすら分からなくなる
 */
function drawEdge(edge, pos, index = 0, parallel = 0) {
  const a = pos.get(edge.from);
  const b = pos.get(edge.to);
  if (!a || !b) return null;
  const start = edgePoint(a, b);
  const end = edgePoint(b, a);

  // 同じ層のノード同士は、間にある箱を避けて弧で結ぶ
  const sameRow = Math.abs(a.y - b.y) < 1;
  const t = LABEL_SPOTS[index % LABEL_SPOTS.length];
  const mid = {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  };
  let d = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
  if (sameRow) {
    const lift = Math.min(90, 24 + Math.abs(end.x - start.x) * 0.18) + parallel * 22;
    mid.x = (start.x + end.x) / 2;
    mid.y = start.y - lift * 0.78;
    d = `M ${start.x} ${start.y} Q ${mid.x} ${start.y - lift} ${end.x} ${end.y}`;
  } else if (parallel) {
    // 段をまたぐ 2 本目以降は、線に直交する向きへ膨らませる
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const len = Math.hypot(dx, dy) || 1;
    const off = parallel * 26;
    const cx = (start.x + end.x) / 2 - (dy / len) * off;
    const cy = (start.y + end.y) / 2 + (dx / len) * off;
    mid.x = (start.x + end.x) / 2 - (dy / len) * off * 0.75;
    mid.y = (start.y + end.y) / 2 + (dx / len) * off * 0.75;
    d = `M ${start.x} ${start.y} Q ${cx} ${cy} ${end.x} ${end.y}`;
  }

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
  path.append(svg("title", { text: edgeTitle(edge) }));

  const words = edge.mutual && edge.back && edge.back !== edge.label
    ? `${edge.label} ⇄ ${edge.back}`
    : edge.label;
  if (!words) return svg("g", {}, [path]);
  const text = svg("text", {
    x: mid.x,
    y: mid.y - 6,
    class: "rel-edge-label",
    "text-anchor": "middle",
    text: words,
  });
  return svg("g", {}, [path, text]);
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
  const group = svg("g", { class: cls });
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

function draw(graph) {
  const { nodes, edges } = graph;
  if (!nodes.length) {
    canvas.replaceChildren(
      el("p", { class: "empty", text: "このカテゴリには関係が書かれたエントリがありません。" })
    );
    return;
  }
  const level = levelsOf(nodes, edges);
  const layered = Math.max(...level.values()) > 0;
  const { pos, width, height } = layered
    ? layeredLayout(nodes, edges, level)
    : circleLayout(nodes);

  const root = svg("svg", {
    class: "rel-graph",
    viewBox: `0 0 ${Math.ceil(width)} ${Math.ceil(height)}`,
    width: Math.ceil(width),
    height: Math.ceil(height),
    role: "img",
    "aria-label": "用語の相関図",
  });
  root.append(svg("defs", {}, [marker("arrow", "rel-arrowhead")]));
  // 辺を先に置いてノードを上に重ねる (線がラベルを横切らないように)
  const pairs = new Map();   // 同じ 2 ノードを結ぶ辺の本数を数えながら描く
  edges.forEach((edge, i) => {
    const key = [edge.from, edge.to].sort().join(" ");
    const parallel = pairs.get(key) || 0;
    pairs.set(key, parallel + 1);
    root.append(drawEdge(edge, pos, i, parallel));
  });
  for (const node of nodes) root.append(drawNode(node, pos));
  canvas.replaceChildren(root);
}

// --------------------------------------------------------------------------- //
// 読み込み
// --------------------------------------------------------------------------- //

const params = new URLSearchParams(location.search);
let currentScope = params.get("scope") || "";

async function loadCategories() {
  const tree = await api("/api/categories").catch(() => []);
  const withEntries = tree.filter((n) => n.count > 0);
  categorySelect.replaceChildren(
    el("option", { value: "", text: "すべてのカテゴリ" }),
    ...withEntries.map((n) =>
      el("option", {
        value: `${n.scope} ${n.category}`,
        text: n.scope === "local" ? `📁 ${n.category}` : n.category,
      })
    )
  );
  const wanted = params.get("category");
  if (wanted) {
    const hit = withEntries.find(
      (n) => n.category === wanted && (!currentScope || n.scope === currentScope)
    );
    if (hit) categorySelect.value = `${hit.scope} ${hit.category}`;
  }
}

function selection() {
  if (!categorySelect.value) return { category: null, scope: null };
  const [scope, category] = categorySelect.value.split(" ");
  return { category, scope };
}

function paintNotes(graph) {
  const lines = [];
  if (graph.hidden) {
    // 黙って伏せない。何本隠しているかは必ず出す
    lines.push(
      `判明位置が書かれた関係を ${graph.hidden} 本伏せています（上のチェックで出せます）。`
    );
  }
  for (const b of graph.broken) {
    lines.push(`「${b.from_term}」→「${b.to}」が解決できません: ${b.reason}`);
  }
  notes.hidden = !lines.length;
  notes.textContent = lines.join(" / ");
}

async function refresh() {
  const { category, scope } = selection();
  setStatus(statusNode, "読み込み中", "busy");
  const query = new URLSearchParams();
  if (category) query.set("category", category);
  if (scope) query.set("scope", scope);
  if (spoilerCheck.checked) query.set("spoilers", "true");
  try {
    const graph = await api(`/api/graph?${query}`);
    draw(graph);
    paintNotes(graph);
    const shown = graph.edges.length;
    setStatus(statusNode, `${graph.nodes.length} 語 / ${shown} 本の関係`);
    legend.textContent =
      "→ は一方的、⇄ は相互。▲▼ の代わりに上下の関係は段で表しています。" +
      "破線の枠はまだ登録されていない語で、押すと辞書で探せます。";
  } catch (err) {
    setStatus(statusNode, err.message, "error");
    canvas.replaceChildren(el("p", { class: "status error", text: err.message }));
  }
}

async function onDraft() {
  const { category, scope } = selection();
  draftButton.disabled = true;
  try {
    // 書き込まれたぶんを図に反映する。0 本でも状態は描き直しておく
    if (await openRelationsDialog({ category: category || "", scope: scope || "" })) {
      await refresh();
    }
  } finally {
    draftButton.disabled = false;
  }
}

async function main() {
  paintEntryCount(countNode);
  await loadCategories();
  categorySelect.addEventListener("change", refresh);
  spoilerCheck.addEventListener("change", refresh);
  draftButton.addEventListener("click", onDraft);
  await refresh();
}

main();
