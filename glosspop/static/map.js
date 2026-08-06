// 地図の見せ方。**位置で置く図**（6 つめ）。
//
// 他の 5 つは配置を計算するが、**この見せ方は計算しない** —— 座標が与えられるので、
// 絵の上に置くだけ。そのぶんいちばん小さい。
//
// **座標が書いてあるものだけが出る。分類しない。** 種別やタグで「どれが地名か」を
// 決めると、**分類の漏れがそのまま図の欠落**になる。書いた＝出したいという意思表示
// なので、機械が推測する余地がない（時系列の「判明位置が書かれているかで切る」と
// まったく同じ考え方）。
//
// **出せなかったものは数える。** 座標の無い語、別の絵にいる語、片端が地図に無い関係。
// `hidden` / `outside` / `undated` と同じ約束で、黙って欠けた図を出さない。
//
// **絵には地名が書かれていない。** 名前は辞書が持っているので、焼き込むと二重になり、
// 改名したときに食い違う。絵は地形だけで、名前はこちらが置く。
import {
  describeNode, describeRelation, estTextWidth, relationWords, svgEl as svg,
} from "./base.js";

//: 絵の幅。**座標は「絵の幅を 1 とした比」**なので、ここが唯一の基準になる。
//: 縦横それぞれに 0〜1 を割り当てないのは、縦横比の違う絵へ差し替えたときに
//: 点が歪むため（幅基準なら、同じ地図の高解像度版に差し替えても点は動かない）
const W = 1000;

//: 絵の高さが分かるまでの仮の値。**点の位置はこれに影響されない**（y も幅基準）
const GUESS_RATIO = 0.7;

const PAD = 40;
const DOT_R = 7;
const FONT = 12;
const NAME_MAX = 14;
const WORDS_MAX = 14;

function clip(text, max) {
  const chars = [...String(text || "")];
  return chars.length > max ? `${chars.slice(0, max).join("")}…` : chars.join("");
}

function arrowMarker() {
  return svg("defs", {}, [
    svg("marker", {
      id: "map-arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 6,
      markerHeight: 6,
      orient: "auto-start-reverse",
    }, [svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "rel-arrow" })]),
  ]);
}

/** その絵に置けるノードだけを拾う。**両方書いてあるものだけ**が地図に出る。 */
function placed(nodes, chosen) {
  const pos = new Map();
  for (const node of nodes) {
    if (!node.map || node.map !== chosen.name) continue;
    if ((node.scope || "global") !== chosen.scope) continue;
    const pin = node.pin || [];
    if (pin.length !== 2) continue;
    pos.set(node.ref, { x: pin[0] * W, y: pin[1] * W, node });
  }
  return pos;
}

/**
 * 地図を描く。戻り値は他の見せ方と同じ ``{ root, box, ... }``。
 *
 * ``opts.onResize`` は**絵の高さが分かったときに呼ぶ**。絵の縦横比は読み込むまで
 * 分からないので、先に仮の高さで描いておき、届いたら `box` を書き換えて呼び直す
 * （**点の位置は最初から正しい** —— 座標が幅基準なので高さに依らない）。
 */
export function buildMap(graph, opts = {}) {
  const { nodes, edges, maps = [] } = graph;
  const { onEdge, onResize, mapName } = opts;

  // **どの絵を出すかは、出ている語から決める。** いちばん多く点が乗る絵を既定にし、
  // 選ばれているものは必ず注意書きに出す（相関図の範囲と同じ約束）
  const chosen = maps.find((m) => `${m.scope}/${m.name}` === mapName)
    || [...maps].sort((a, b) => b.count - a.count)[0];

  const byRef = new Map(nodes.map((n) => [n.ref, n]));
  const pos = placed(nodes, chosen);
  const height = Math.round(W * GUESS_RATIO);
  const box = { x: -PAD, y: -PAD, w: W + PAD * 2, h: height + PAD * 2 };

  const root = svg("svg", {
    class: "rel-graph rel-map",
    width: "100%",
    height: "100%",
    viewBox: `${box.x} ${box.y} ${box.w} ${box.h}`,
  });
  root.append(arrowMarker());

  // **絵は `<image>` に入れる。CSS の背景にしない** —— `viewBox` を動かす方式なので、
  // 中に入れれば画像と線と語が一緒に拡大縮小・移動する。背景にすると 2 つを別々に
  // 動かす同期処理が要り、`graph.js` の資産が使えなくなる。
  // **`<image>` 経由なら SVG の中のスクリプトは動かない**（secure static mode）
  const image = svg("image", {
    href: chosen.url,
    x: 0, y: 0, width: W, height,
    preserveAspectRatio: "xMinYMin meet",
    class: "rel-map-bg",
  });
  root.append(image);

  // 絵の縦横比が届いたら高さを直し、入れ物に合わせ直してもらう
  const probe = new Image();
  probe.addEventListener("load", () => {
    if (!probe.naturalWidth || !probe.naturalHeight) return;
    const real = Math.round((W * probe.naturalHeight) / probe.naturalWidth);
    image.setAttribute("height", real);
    box.h = real + PAD * 2;               // graph.js が持っているのと同じオブジェクト
    root.setAttribute("viewBox", `${box.x} ${box.y} ${box.w} ${box.h}`);
    onResize?.();
  });
  probe.src = chosen.url;

  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  const touching = new Map([...pos.keys()].map((ref) => [ref, []]));
  let offEdges = 0;

  for (const edge of edges) {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    if (!a || !b) {
      // 片端が地図に無い関係。**落とすが必ず数える**
      offEdges++;
      continue;
    }
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
        x: Math.min(a.x, b.x) - 7, y: Math.min(a.y, b.y) - 7,
        width: Math.abs(b.x - a.x) + 14, height: Math.abs(b.y - a.y) + 14,
      }),
      svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "rel-edge-hit" }),
      svg("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        class: cls,
        "marker-end": "url(#map-arrow)",
        "marker-start": edge.mutual ? "url(#map-arrow)" : null,
      }),
    ]);
    group.append(svg("title", { text: `${detail}（押すと直せます）` }));

    const words = relationWords(edge);
    const text = words
      ? svg("text", {
        class: "rel-edge-label",
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2 - 6,
        "text-anchor": "middle",
        "data-detail": detail,
        text: clip(words, WORDS_MAX),
      })
      : null;
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

  // **点は丸と名前。箱にしない** —— 箱で地形を覆うと、位置で置いた意味が減る
  const nodeGroups = new Map();
  for (const [ref, p] of pos) {
    const node = p.node;
    const term = node.term || ref;
    const label = clip(term, NAME_MAX);
    const group = svg("g", {
      class: ["rel-node", "rel-map-pin", node.missing ? "missing" : ""].filter(Boolean).join(" "),
      "data-detail": describeNode(node),
    }, [
      svg("a", { href: node.url || `/glossary?q=${encodeURIComponent(term)}` }, [
        svg("circle", { cx: p.x, cy: p.y, r: DOT_R }),
        // 名前は点の下。**背の板を敷く**（地形の上だと文字が読めない）
        svg("rect", {
          class: "rel-map-plate",
          x: p.x - estTextWidth(label, FONT) / 2 - 5,
          y: p.y + DOT_R + 3,
          width: estTextWidth(label, FONT) + 10,
          height: FONT + 7,
          rx: 4,
        }),
        svg("text", {
          x: p.x, y: p.y + DOT_R + 3 + (FONT + 7) / 2,
          "text-anchor": "middle", "dominant-baseline": "central",
          text: label,
        }),
        svg("title", {
          text: [term, node.path_label, node.summary].filter(Boolean).join(" — "),
        }),
      ]),
    ]);
    root.append(group);
    nodeGroups.set(ref, group);
  }

  installFocus(root, nodeGroups, touching);

  // **出していないものは全部数える。** どの絵を出しているかも必ず書く
  const others = maps.filter((m) => m !== chosen);
  const noCoords = nodes.filter((n) => !n.map || (n.pin || []).length !== 2).length;
  const elsewhere = nodes.length - pos.size - noCoords;
  const note = [
    `「${chosen.name}」の上に ${pos.size} 語を置いています。`,
    noCoords ? `座標が書かれていない ${noCoords} 語は出していません。` : "",
    elsewhere ? `別の絵にいる ${elsewhere} 語は出していません。` : "",
    offEdges ? `片端が地図に無い関係を ${offEdges} 本伏せています。` : "",
    others.length ? `ほかに ${others.map((m) => `「${m.name}」`).join("")} があります。` : "",
    pos.size ? "" : "この絵に置かれた語がありません。",
  ].filter(Boolean).join("");

  return { root, box, lonely: 0, tucked: 0, note, map: `${chosen.scope}/${chosen.name}` };
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
