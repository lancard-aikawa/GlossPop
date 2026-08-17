// 1 語を中心にした見せ方（ego network）。**選んだ語を真ん中に置き、そこから
// 何つ先までかを選んで環に並べる**（既定は 2 つ先）。
//
// **この見せ方だけができること: 規模に依らない。** 他の 4 つはどれも辞書
// （またはその文書）を一度に出すので、語が増えれば必ず苦しくなる。中心を決めれば
// 絵の大きさは**その語の近所の広さ**で決まり、辞書が何語になっても変わらない。
// 実際の使い方（「この人物は誰とどう関わるか」）にもいちばん近い。
//
// **上下は捨てない。** 段の図と同じ `levelsOf()` で段を出し、中心より上の段は
// 図の上半分、下の段は下半分、同じ段（対等）は左右に置く。**見せ方を変えても
// 言っていることを変えない**という約束は、ここでも同じ。
//
// **出していないものは必ず数える。** 選んだ深さより遠い語、相手が出ていない関係、
// 多すぎて置けなかった語 —— 全部まとめて凡例に出す（`hidden` や
// `outside` と同じ約束で、黙って欠けた図を出さない）。
import {
  describeNode, describeRelation, estTextWidth, relationWords, svgEl as svg,
} from "./base.js";
import { levelsOf, seedOrder } from "./graph-model.js";

const PAD = 28;
const NODE_H = 34;
const NODE_FONT = 12;
const NODE_PAD = 24;
const NODE_MIN_W = 72;
const NODE_MAX_W = 200;
const GAP = 18;              // 環の上で隣り合う箱のあいだ
const RING_GAP = 130;        // 環と環の距離の下限
const MIN_R1 = 150;

//: 中心の語の箱。まわりより一回り大きくして、どれが中心かを形でも示す
const CENTER_H = 42;
const CENTER_FONT = 14;

//: 名前に立てる字数の上限。切ったことは「…」で分かるし、全文は下の枠に出る
const NAME_MAX = 14;
const WORDS_MAX = 14;

//: 2 つ先より遠くに置く語の上限（**環ごとではなく合計**）。**規模に依らない**のが
//: この見せ方の取り柄なので、近所が広すぎるときは切る。**切ったことは必ず数えて返す**。
//: **1 つ先は切らない** —— 直接の関係を落とすと、この図がいちばん答えるはずの問い
//: （「この語は誰とどう関わるか」）が欠ける。環ごとの上限にすると、深さを増やした
//: ぶんだけ天井も増えて「規模に依らない」が効かなくなる
const MAX_FAR = 120;

//: 何つ先まで出すか。**既定は 2。** 1 は用語ページの関係一覧とほぼ同じで、
//: 3 以上は密な辞書だと辞書全体に近づいて取り柄（規模に依らない）が薄れる ——
//: **それでも選べるようにしてある**。「この人物の直接の関係だけ」を見たいときと、
//: 「もう一歩だけ広く」を見たいときが実際にあり、**どちらも既定では出せない**
export const DEPTHS = [1, 2, 3, 4];
export const DEFAULT_DEPTH = 2;

/** 読めない深さは既定に落とす（覚えている値・URL のどちらから来ても通す口）。 */
export function normalizeDepth(value) {
  const n = Number(value);
  return DEPTHS.includes(n) ? n : DEFAULT_DEPTH;
}

//: 段（上下）ごとの置き場所。角度は**12 時から時計回り**の度
//: （x = sin、y = -cos。SVG の y は下向き）。上の段は 12 時、下の段は 6 時、
//: 同じ段（対等）は 3 時と 9 時のまわりに置く。
const DEG = Math.PI / 180;
const SECTOR_ORDER = ["up", "right", "down", "left"];
const ANCHOR = { up: 0, right: 90, down: 180, left: 270 };

function clip(text, max) {
  const chars = [...String(text || "")];
  return chars.length > max ? `${chars.slice(0, max).join("")}…` : chars.join("");
}

function nodeWidth(term, font = NODE_FONT) {
  return Math.max(
    NODE_MIN_W,
    Math.min(NODE_MAX_W, estTextWidth(clip(term, NAME_MAX), font) + NODE_PAD),
  );
}

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
 * 中心にする語を決める。
 *
 * 指された語（`?ref=` か、図の中で押された語）がいまの図に居ればそれ。無ければ
 * **いちばん多く繋がっている語**に落とす —— 何を中心にするか決められないときに
 * 空の図を出すより、その辞書でいちばん語れる語を出したほうがよい。同点は
 * サーバが返した順で決める（乱数も時刻も混ぜないので、同じ辞書なら同じ絵）。
 */
export function resolveCenter(nodes, edges, wanted) {
  if (wanted && nodes.some((n) => n.ref === wanted)) return wanted;
  const degree = new Map(nodes.map((n) => [n.ref, 0]));
  for (const e of edges) {
    degree.set(e.from, (degree.get(e.from) || 0) + 1);
    degree.set(e.to, (degree.get(e.to) || 0) + 1);
  }
  let best = nodes[0]?.ref || "";
  for (const node of nodes) {
    if ((degree.get(node.ref) || 0) > (degree.get(best) || 0)) best = node.ref;
  }
  return best;
}

/**
 * 中心から ``depth`` つ先までの語を、環ごとに集める。
 *
 * `parent` は**1 つ内側のどれから伸びたか**。次の環をその親と同じ側へ寄せるのに
 * 使う（番号で機械的に割ると、左に居る親から右端まで線が図を横切る）。
 *
 * **同じ語は最初に届いた環にだけ置く**（幅優先）。中心からの遠さがその語の
 * 段になるので、あとの環で置き直すと近さが嘘になる。
 */
function ringsOf(center, edges, depth) {
  const near = new Map();                         // ref → 隣の ref の集合
  const add = (a, b) => {
    if (!near.has(a)) near.set(a, new Set());
    near.get(a).add(b);
  };
  for (const e of edges) {
    add(e.from, e.to);
    add(e.to, e.from);
  }
  const seen = new Set([center]);
  const rings = [];
  let frontier = [center];
  let far = 0;                                    // 2 つ先より遠くに置いた数
  let dropped = 0;
  for (let step = 0; step < depth; step++) {
    const ring = [];
    for (const parent of frontier) {
      for (const ref of near.get(parent) || []) {
        if (seen.has(ref)) continue;
        seen.add(ref);
        // **切ったことは数えて返す**（黙って欠けた図を出さない）。
        // 上限を見るのは 2 つ先から —— 1 つ先は直接の関係なので切らない
        if (step && far >= MAX_FAR) {
          dropped++;
          continue;
        }
        if (step) far++;
        ring.push({ ref, parent });
      }
    }
    if (!ring.length) break;                      // これ以上は伸びない
    rings.push(ring);
    frontier = ring.map((item) => item.ref);
  }
  return { rings, dropped };
}

/**
 * 環の上に角度を配る。**段（上下）で置き場所を決め、その中で順に並べる。**
 *
 * 中より上の段は上半分、下の段は下半分、同じ段は左右。段の図で「上にあるものが
 * 上位」と言っている以上、ここでも同じことを言わせる。
 */
function spread(items, { level, center, order }) {
  const groups = { up: [], right: [], down: [], left: [] };
  const mid = [];
  for (const item of items) {
    const diff = (level.get(item.ref) ?? 0) - (level.get(center) ?? 0);
    if (diff < 0) groups.up.push(item);
    else if (diff > 0) groups.down.push(item);
    else mid.push(item);
  }
  // 対等は左右へ分ける（片側に寄せると、反対側が空いたまま片側だけ混む）。
  // **2 つ先は繋がっている相手と同じ側へ**（`at` は 1 つ先の親の角度）——
  // 番号で機械的に割ると、左に居る親から右端まで線が図を横切る
  const hinted = mid.length && mid.every((item) => typeof item.at === "number");
  if (hinted) {
    groups.right = mid.filter((item) => Math.sin(item.at) >= 0);
    groups.left = mid.filter((item) => Math.sin(item.at) < 0);
  } else {
    const half = Math.ceil(mid.length / 2);
    groups.right = mid.slice(0, half);
    groups.left = mid.slice(half);
  }

  // **空いた向きは隣が引き取る。** 上下の語が無いときに 4 分の 1 ずつ固定で
  // 持たせると、対等ばかりの辞書（実際よくある）で左右の扇だけが混み、上下は
  // 空いたままになる。それぞれの向きに「いちばん近い扇」を配ると円が埋まり、
  // **どの群も自分の向きのまわりに残る**（上にあるものが上位、は変わらない）
  const present = SECTOR_ORDER.filter((name) => groups[name].length);
  const angles = new Map();
  let needed = 0;
  present.forEach((name, i) => {
    const list = groups[name];
    const prev = ANCHOR[present[(i - 1 + present.length) % present.length]];
    const next = ANCHOR[present[(i + 1) % present.length]];
    const back = ((ANCHOR[name] - prev + 360) % 360) || 360;
    const forward = ((next - ANCHOR[name] + 360) % 360) || 360;
    const from = ANCHOR[name] - back / 2;
    const span = (back + forward) / 2;
    // 隣り合う順は、繋がっているものが近くに来る順（親の角度があればそれを優先）
    list.sort((a, b) => (a.at ?? order.get(a.ref) ?? 0) - (b.at ?? order.get(b.ref) ?? 0));
    list.forEach((item, j) => {
      angles.set(item.ref, (from + span * ((j + 0.5) / list.length)) * DEG);
    });
    // 半径は**いちばん詰まっている扇**で決める（弧の長さが箱の幅を下回らないこと）
    const width = Math.max(...list.map((item) => nodeWidth(item.term) + GAP));
    needed = Math.max(needed, (width * list.length) / (span * DEG));
  });
  return { angles, needed };
}

/**
 * 1 語を中心にした図を組み立てる。返すのは他の見せ方と同じ ``{ root, box }``。
 *
 * @param {object} graph  `/api/graph` の返り値
 * @param {function} onEdge   関係を押したときに呼ぶ（編集ダイアログ）
 * @param {string}   center   中心にしたい ref（無ければいちばん多く繋がっている語）
 * @param {function} onCenter まわりの語を押したときに呼ぶ（中心を移す）
 * @param {number}   depth    何つ先まで出すか（既定は 2）
 */
export function buildEgo(graph, { onEdge, center: wanted, onCenter, depth } = {}) {
  const { nodes, edges } = graph;
  const byRef = new Map(nodes.map((n) => [n.ref, n]));
  const center = resolveCenter(nodes, edges, wanted);
  const steps = normalizeDepth(depth);
  const { rings, dropped } = ringsOf(center, edges, steps);

  // 段は**図全体**から出す（中心を移しても上下が変わらないように）
  const level = levelsOf(nodes, edges);
  const order = seedOrder(nodes, edges);

  // 位置。中心は原点で、あとで枠ぶんだけずらす
  const pos = new Map([[center, { x: 0, y: 0, w: nodeWidth(byRef.get(center)?.term || center, CENTER_FONT), h: CENTER_H, ring: 0 }]]);
  const place = (ref, angle, radius, ring) => {
    const node = byRef.get(ref);
    pos.set(ref, {
      x: Math.sin(angle) * radius,
      y: -Math.cos(angle) * radius,
      w: nodeWidth(node?.term || ref),
      h: NODE_H,
      ring,
    });
  };

  // 環は内側から順に決める。**半径は 1 つ内側より必ず外**（詰まっている扇に
  // 合わせて広げるので、外の環ほど大きくなる）。角度は覚えておいて、次の環を
  // 親と同じ側へ寄せるのに使う
  const angles = new Map();
  const radii = [];
  let radius = 0;
  rings.forEach((ring, i) => {
    const laid = spread(
      ring.map(({ ref, parent }) => ({
        ref,
        term: byRef.get(ref)?.term || ref,
        // 1 つ先には親（＝中心）の向きが無いので、いつもどおり左右へ分ける
        at: i ? angles.get(parent) : undefined,
      })),
      { level, center, order },
    );
    radius = i ? Math.max(radius + RING_GAP, laid.needed) : Math.max(MIN_R1, laid.needed);
    radii.push(radius);
    for (const [ref, angle] of laid.angles) angles.set(ref, angle);
    for (const { ref } of ring) place(ref, laid.angles.get(ref) ?? 0, radius, i + 1);
  });
  //: いちばん外の環。**枠はここから作る**（環の目印まで入れないと、語の無い
  //: 向きで円がはみ出して切れる）
  const outer = radii[radii.length - 1] || MIN_R1;

  // 描くのは**両端が出ている関係すべて**。相手が出ていないものは数えて返す
  const shown = edges.filter((e) => pos.has(e.from) && pos.has(e.to));
  const outside = edges.length - shown.length;

  let minX = -outer - PAD;
  let minY = -outer - PAD;
  let maxX = outer + PAD;
  let maxY = outer + PAD;
  for (const p of pos.values()) {
    minX = Math.min(minX, p.x - p.w / 2 - PAD);
    maxX = Math.max(maxX, p.x + p.w / 2 + PAD);
    minY = Math.min(minY, p.y - p.h / 2 - PAD);
    maxY = Math.max(maxY, p.y + p.h / 2 + PAD);
  }
  const width = maxX - minX;
  const height = maxY - minY;

  const root = svg("svg", {
    class: "rel-graph rel-ego",
    width: "100%",
    height: "100%",
    viewBox: `${minX} ${minY} ${Math.ceil(width)} ${Math.ceil(height)}`,
    role: "img",
    "aria-label": `用語の相関図（${byRef.get(center)?.term || ""} を中心にした見せ方）`,
  });
  root.append(svg("defs", {}, [marker("ego-arrow", "rel-arrowhead")]));

  // 環の目印。**飾りは当たり判定を持たない**（線やノードの上に乗ると押せなくなる）
  const guides = svg("g", { class: "ego-guides" });
  for (const r of radii) guides.append(svg("circle", { cx: 0, cy: 0, r }));
  root.append(guides);

  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  const touching = new Map([...pos.keys()].map((ref) => [ref, []]));
  let tucked = 0;

  for (const edge of shown) {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    const [p, q] = [touchPoint(a, b), touchPoint(b, a)];
    const detail = describeRelation(edge, {
      from: byRef.get(edge.from)?.term, to: byRef.get(edge.to)?.term,
    });
    const cls = ["rel-edge", edge.missing ? "missing" : "", edge.reveal ? "reveal" : ""]
      .filter(Boolean).join(" ");
    const group = svg("g", {
      class: "rel-edge-group",
      tabindex: "0",
      role: "button",
      "aria-label": `関係を直す: ${detail}`,
      "data-detail": detail,
    }, [
      // **押せる範囲を外形としても持たせる**（真横・真縦の線は外形が潰れる）
      svg("rect", {
        class: "rel-edge-band",
        x: Math.min(p.x, q.x) - 7, y: Math.min(p.y, q.y) - 7,
        width: Math.abs(q.x - p.x) + 14, height: Math.abs(q.y - p.y) + 14,
      }),
      // 線は細くて押せない。透明な太い線を重ねて当たり判定にする
      svg("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: "rel-edge-hit" }),
      svg("line", {
        x1: p.x, y1: p.y, x2: q.x, y2: q.y,
        class: cls,
        "marker-end": "url(#ego-arrow)",
        "marker-start": edge.mutual ? "url(#ego-arrow)" : null,
      }),
    ]);
    group.append(svg("title", { text: `${detail}（押すと直せます）` }));

    // **一言は中心につながる線にだけ出す。** 外側まで全部出すと、環の外周で
    // 文字が折り重なって**重なった 2 つとも読めなくなる**。畳んだものは消して
    // いない —— 本数を凡例に出し、線かその語に乗せれば出る（段の図と同じ約束）
    const words = relationWords(edge);
    const spoke = edge.from === center || edge.to === center;
    // **中心から離れたところに置く。** 線は全部中心に集まるので、真ん中に
    // 置くと一言どうしが根元で折り重なる（実際にそうなった）
    const inner = edge.from === center ? p : q;
    const outer = edge.from === center ? q : p;
    const at = spoke ? 0.62 : 0.5;
    const text = words
      ? svg("text", {
        class: spoke ? "rel-edge-label" : "rel-edge-label tucked",
        x: inner.x + (outer.x - inner.x) * at,
        y: inner.y + (outer.y - inner.y) * at - 4,
        "text-anchor": "middle",
        "data-detail": detail,
        text: clip(words, WORDS_MAX),
      })
      : null;
    if (text && !spoke) tucked++;
    lines.append(group);
    if (text) labels.append(text);

    const parts = [group, text].filter(Boolean);
    const light = (on) => {
      for (const node of parts) node.classList.toggle("hot", on);
    };
    const open = () => onEdge?.(edge);
    for (const node of parts) {
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
      touching.get(ref)?.push({ parts, other: ref === edge.from ? edge.to : edge.from });
    }
  }
  root.append(lines, labels);

  const nodeGroups = new Map();
  for (const [ref, p] of pos) {
    const node = byRef.get(ref);
    const term = node?.term || ref;
    const middle = ref === center;
    // `ego-far` は**2 つ先より遠いぶん全部**（薄くする側）。`ego-ringN` は
    // そのうえでの遠さで、CSS はどちらも使う —— 環の目印だけだと読み落とす
    const cls = [
      "rel-node",
      middle ? "ego-center" : `ego-ring${p.ring}`,
      !middle && p.ring > 1 ? "ego-far" : "",
      node?.missing ? "missing" : "", node?.outside ? "outside" : "",
    ].filter(Boolean).join(" ");
    const shape = [
      svg("rect", {
        x: p.x - p.w / 2, y: p.y - p.h / 2, width: p.w, height: p.h, rx: middle ? 12 : 9,
      }),
      svg("text", {
        x: p.x, y: p.y, "text-anchor": "middle", "dominant-baseline": "central",
        text: clip(term, NAME_MAX),
      }),
    ];
    const summary = [term, node?.path_label, node?.summary].filter(Boolean).join(" — ");
    // **中心は辞書ページへの link、まわりは「押すと中心が移る」ボタン。**
    // 1 回押して中心に持ってきて、もう 1 回押せば辞書ページ —— たどりながら
    // 読める形にしてある（まわりを link にすると、隣を覗くたびに図から出る）
    const group = middle
      ? svg("g", { class: cls, "data-detail": describeNode(node || { term }) }, [
        svg("a", { href: node?.url || `/glossary?q=${encodeURIComponent(term)}` }, [
          ...shape, svg("title", { text: `${summary}（押すと辞書ページへ）` }),
        ]),
      ])
      : svg("g", {
        class: cls,
        tabindex: "0",
        role: "button",
        "aria-label": `${term} を中心にする`,
        "data-detail": `${describeNode(node || { term })}（押すと中心が移ります）`,
      }, [...shape, svg("title", { text: `${summary}（押すと中心が移ります）` })]);
    if (!middle) {
      const move = () => onCenter?.(ref);
      group.addEventListener("click", move);
      group.addEventListener("keydown", (ev) => {
        if (ev.key !== "Enter" && ev.key !== " ") return;
        ev.preventDefault();
        move();
      });
    }
    root.append(group);
    nodeGroups.set(ref, group);
  }

  installFocus(root, nodeGroups, touching);

  // **出していないものは全部数える。** 何語の辞書から切り出した近所なのかが
  // 分からないと、この図だけを見て「関係はこれで全部」と読まれる
  const away = nodes.length - pos.size;
  const note = [
    `「${byRef.get(center)?.term || ""}」から ${steps} つ先までを出しています`
    + `（${pos.size} 語）。まわりの語を押すとそこが中心になります。`,
    away ? `ほか ${away} 語は ${steps} つ先より遠いので出していません。` : "",
    dropped ? `近所が広いので ${dropped} 語は置いていません。` : "",
    outside ? `相手がここに出ていない関係を ${outside} 本伏せています。` : "",
    rings.length ? "" : "この語にはまだ関係が書かれていません。",
  ].filter(Boolean).join("");

  return {
    root,
    box: { x: minX, y: minY, w: width, h: height },
    lonely: 0,
    tucked,
    note,
    center,
    // 実際に出した深さ（読めない値は既定に落ちているので、呼ぶ側はこれを見る）
    depth: steps,
  };
}

/** ``a`` の箱の縁のうち、``b`` へ向かう側の点。線が箱に潜り込まないように。 */
function touchPoint(a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (!dx && !dy) return { x: a.x, y: a.y };
  const sx = dx ? (a.w / 2 + 2) / Math.abs(dx) : Infinity;
  const sy = dy ? (a.h / 2 + 2) / Math.abs(dy) : Infinity;
  const t = Math.min(sx, sy);
  return { x: a.x + dx * t, y: a.y + dy * t };
}

/** 1 つの語に乗せている間、その語の関係だけを濃く出す（他の見せ方と同じ作法）。 */
function installFocus(root, nodeGroups, touching) {
  const light = (ref, on) => {
    root.classList.toggle("focusing", on);
    if (!on) {
      for (const node of root.querySelectorAll(".lit")) node.classList.remove("lit");
      return;
    }
    nodeGroups.get(ref)?.classList.add("lit");
    for (const { parts, other } of touching.get(ref) || []) {
      for (const node of parts) node.classList.add("lit");
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
