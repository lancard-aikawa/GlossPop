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
// **絵に地名を書かないのが望ましいが、強制はできない。** 名前は辞書が持っているので
// 焼き込むと二重になるが、AI に描かせた地図には入っているのが普通 ——
// **名前を出すかを切れる**ようにしてある（消しても点も線も残り、乗せれば名前は出る）。
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

/** 点の並びの真ん中あたり。線は中ほど、領域は頂点の平均（名前と辺の付け根）。 */
function anchorOf(kind, pts) {
  if (kind === "point") return pts[0];
  if (kind === "area") {
    const n = pts.length;
    return {
      x: pts.reduce((s, q) => s + q.x, 0) / n,
      y: pts.reduce((s, q) => s + q.y, 0) / n,
    };
  }
  const mid = (pts.length - 1) / 2;
  const a = pts[Math.floor(mid)];
  const b = pts[Math.ceil(mid)];
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/**
 * その絵に置けるノードだけを拾う。**形が書いてあるものだけ**が地図に出る。
 *
 * 種別は `map_shape` がサーバ側で 1 つに畳んである（点・線・領域）ので、
 * ここでは**場合分けを持たない** —— 3 通りを描く側でもう一度分けると、
 * 足したときに片方だけ直し忘れる。
 */
function placed(nodes, chosen) {
  const pos = new Map();
  for (const node of nodes) {
    if (!node.map || node.map !== chosen.name) continue;
    if ((node.scope || "global") !== chosen.scope) continue;
    const shape = node.shape;
    if (!shape || !shape.points?.length) continue;
    const pts = shape.points.map(([x, y]) => ({ x: x * W, y: y * W }));
    pos.set(node.ref, { kind: shape.kind, pts, ...anchorOf(shape.kind, pts), node });
  }
  return pos;
}

const asPoints = (pts) => pts.map((q) => `${q.x},${q.y}`).join(" ");

/**
 * 文字を置く四角。**実測より少し大きく見積もる**（`graph.js` の `labelBox` と
 * 同じ理由 —— 小さいと、置いたあとで触れる）。
 */
function textBox(text, x, top, size) {
  const w = estTextWidth(text, size) + 10;
  return { x: x - w / 2, y: top, w, h: size + 7 };
}

const overlaps = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * 空いていれば取る。**取れなければ畳む**（`false` を返す）。
 *
 * **消しはしない。** 地図は座標が与えられている図なので、段の図のように
 * 「空いている場所へ逃がす」ことができない —— 動かせば別の場所を指してしまう。
 * 残る手は重ねるか畳むかで、**重ねると重なった 2 つとも読めず、下の絵まで隠す**。
 * 畳んだものは乗せれば出るし、本数は凡例と注意書きに出す。
 */
function takeSpot(box, taken) {
  if (taken.some((other) => overlaps(box, other))) return false;
  taken.push(box);
  return true;
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
  // ``labels`` は**一言の層**の名前で既に使っている（下）ので、受け取り側で
  // 名前を変える —— 同じ関数の中で 2 つ宣言すると読み込みごと落ちる（実際に踏んだ）
  const {
    onEdge, onResize, mapName, hidden, labels: showNames = true,
    editing = false, placing = "", onMove, onPlace, onRefuse,
  } = opts;

  // **どの絵を出すかは、出ている語から決める。** いちばん多く点が乗る絵を既定にし、
  // 選ばれているものは必ず注意書きに出す（相関図の範囲と同じ約束）
  const chosen = maps.find((m) => `${m.scope}/${m.name}` === mapName)
    || [...maps].sort((a, b) => b.count - a.count)[0];

  const byRef = new Map(nodes.map((n) => [n.ref, n]));
  // **一覧は絞る前に作る。** 外したものも並べないと、チェックを戻せなくなる
  const all = placed(nodes, chosen);
  const items = [...all].map(([ref, p]) => ({
    ref, term: p.node.term || ref, category: p.node.category || "", kind: p.kind,
  }));
  // **絵の名前だけ書いて形が無い語 = 置き待ち。** 分類していない ——
  // 「この絵に置きたい」と書いてあるものだけを出すので、辞書全体は並ばない
  const pending = nodes
    .filter((n) => n.map === chosen?.name && (n.scope || "global") === chosen?.scope)
    .filter((n) => !n.shape)
    .map((n) => ({ ref: n.ref, term: n.term || n.ref, category: n.category || "" }));
  const pos = new Map([...all].filter(([ref]) => !hidden?.has(ref)));
  const unchecked = all.size - pos.size;
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

  // **置き場所の取り合いを先に決める。** 座標は動かせないので、重なったものは
  // 畳むしかない（→ `takeSpot`）。**点そのものを先に取っておく** —— 名前の板が
  // 別の語の点を覆うと、その語が図から消えたのと同じことになる
  const taken = [...pos.values()]
    .filter((p) => p.kind === "point")
    .map((p) => ({ x: p.x - DOT_R, y: p.y - DOT_R, w: DOT_R * 2, h: DOT_R * 2 }));
  const drawable = edges
    .map((edge) => ({ edge, a: pos.get(edge.from), b: pos.get(edge.to) }))
    .filter(({ a, b }) => a && b);
  const offEdges = edges.length - drawable.length;   // 片端が地図に無い関係

  // **名前が先、一言は後。** 名前はその語が何なのかを決めるもので、一言は
  // 関係の補足。両方は置けないとき、残すべきなのは名前のほう。
  // 名前どうしの順は**関係の多い語から**（図の骨になる語を残す）。同数のときは
  // 上から左から —— 並びを決め切らないと、開くたびに畳む語が入れ替わる
  const degree = new Map([...pos.keys()].map((ref) => [ref, 0]));
  for (const { edge } of drawable) {
    degree.set(edge.from, degree.get(edge.from) + 1);
    degree.set(edge.to, degree.get(edge.to) + 1);
  }
  const nameSpots = new Map();
  let tuckedNames = 0;
  if (showNames) {
    const order = [...pos].sort(([refA, a], [refB, b]) =>
      (degree.get(refB) - degree.get(refA)) || (a.y - b.y) || (a.x - b.x));
    for (const [ref, p] of order) {
      const label = clip(p.node.term || ref, NAME_MAX);
      // 点だけは名前を丸の下へ逃がす（丸に重ねると点が読めない）
      const top = p.kind === "point" ? p.y + DOT_R + 3 : p.y - (FONT + 7) / 2;
      const box = textBox(label, p.x, top, FONT);
      const tucked = !takeSpot(box, taken);
      if (tucked) tuckedNames++;
      nameSpots.set(ref, { label, box, tucked });
    }
  }

  // 一言は**短い線から**決める（動かせる幅が狭いのはそちら、は段の図と同じ）
  const tuckedEdges = new Set();
  const byLength = [...drawable]
    .map((item, i) => ({ i, ...item }))
    .filter(({ edge }) => relationWords(edge))
    .sort((p, q) => Math.hypot(p.a.x - p.b.x, p.a.y - p.b.y)
      - Math.hypot(q.a.x - q.b.x, q.a.y - q.b.y));
  for (const { i, edge, a, b } of byLength) {
    const words = clip(relationWords(edge), WORDS_MAX);
    const box = textBox(words, (a.x + b.x) / 2, (a.y + b.y) / 2 - 6 - (FONT + 7) / 2, FONT);
    if (!takeSpot(box, taken)) tuckedEdges.add(i);
  }

  for (const [index, { edge, a, b }] of drawable.entries()) {
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
        // 畳んだものも DOM には残す（乗せれば出る。`.rel-edge-label.tucked`）
        class: tuckedEdges.has(index) ? "rel-edge-label tucked" : "rel-edge-label",
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

  // **形は 3 つ、名前の出し方は 1 つ。** 点は丸、線は折れ線、領域は多角形で、
  // どれも名前は真ん中あたりに板を敷いて置く（箱で地形を覆わない）。
  // **重ねる順は 領域 → 線 → 点** —— 領域を後に描くと点を塗りつぶす
  const nodeGroups = new Map();
  const layers = { area: svg("g"), line: svg("g"), point: svg("g") };
  for (const [ref, p] of pos) {
    const node = p.node;
    const term = node.term || ref;
    const body = [];
    if (p.kind === "area") {
      body.push(svg("polygon", { class: "rel-map-fill", points: asPoints(p.pts) }));
    } else if (p.kind === "line") {
      // 線は細くて押せない。透明な太い線を重ねて当たり判定にする（辺と同じ作法）
      body.push(
        svg("polyline", { class: "rel-map-hit", points: asPoints(p.pts) }),
        svg("polyline", { class: "rel-map-route", points: asPoints(p.pts) }),
      );
    } else {
      body.push(svg("circle", { cx: p.x, cy: p.y, r: DOT_R }));
    }
    // 名前の板。置き場所は上で決めてある（点だけ丸の下。丸に重ねると点が読めない）。
    // **消せる。** AI に描かせた絵には地名が焼き込まれているのが普通で、
    // そこへ重ねると二重になる。**消しても情報は失われない** ——
    // 乗せれば図の下の枠と吹き出しに出る（だから「黙って欠ける」にならない）。
    // **重なって畳んだものも同じ**（`.rel-map-name.tucked`。乗せれば出る）
    const spot = nameSpots.get(ref);
    const name = !spot ? [] : [
      svg("g", { class: spot.tucked ? "rel-map-name tucked" : "rel-map-name" }, [
        svg("rect", {
          class: "rel-map-plate",
          x: spot.box.x, y: spot.box.y, width: spot.box.w, height: spot.box.h, rx: 4,
        }),
        svg("text", {
          x: p.x, y: spot.box.y + spot.box.h / 2,
          "text-anchor": "middle", "dominant-baseline": "central",
          text: spot.label,
        }),
      ]),
    ];
    const group = svg("g", {
      class: ["rel-node", "rel-map-pin", `rel-map-${p.kind}`, node.missing ? "missing" : ""]
        .filter(Boolean).join(" "),
      // 呼ぶ側が語を名指しで探せるようにする（用語ページから開いたときの目印）
      "data-ref": ref,
      "data-detail": describeNode(node),
    }, [
      svg("a", { href: node.url || `/glossary?q=${encodeURIComponent(term)}` }, [
        ...body,
        ...name,
        svg("title", {
          text: [term, node.path_label, node.summary].filter(Boolean).join(" — "),
        }),
      ]),
    ]);
    p.group = group;
    layers[p.kind].append(group);
    nodeGroups.set(ref, group);
  }
  root.append(layers.area, layers.line, layers.point);

  installFocus(root, nodeGroups, touching);
  if (editing) installHandles(root, pos, onMove, onRefuse);
  if (placing) {
    // **置くのは絵の上を 1 回押すだけ。** armed の間だけ効く（間違って置かない）
    root.classList.add("is-placing");
    root.addEventListener("click", (ev) => {
      const at = toUser(root, ev);
      if (at) onPlace?.(placing, [[at.x / W, at.y / W]]);
    }, { once: true });
  }

  // **出していないものは全部数える。** どの絵を出しているかも必ず書く
  const kinds = { point: 0, line: 0, area: 0 };
  for (const p of pos.values()) kinds[p.kind]++;
  const others = maps.filter((m) => m !== chosen);
  const noCoords = nodes.filter((n) => !n.map || !n.shape).length;
  const elsewhere = nodes.length - all.size - noCoords;
  const note = [
    `「${chosen.name}」の上に ${pos.size} 語を置いています`
    + `（点 ${kinds.point} / 線 ${kinds.line} / 領域 ${kinds.area}）。`,
    showNames ? "" : "名前は消しています（乗せると出ます）。",
    // **畳んだ名前は必ず数える。** 黙って消すと「登録していない語」に見える
    // （伏せた本数を返すのと同じ約束。一言のほうは凡例が出す）
    tuckedNames
      ? `重なって置けない名前 ${tuckedNames} 個は畳んでいます（乗せると出ます）。` : "",
    editing
      ? "丸を掴むと動かせます（矢印キーでも。離すと保存されます）。"
        + "線の上の小さな丸で頂点を足し、丸に乗せて出る ✕ で消せます"
        + "（キーボードでは ＋ と Delete）。"
      : "",
    placing ? "絵の上を押すとそこへ置きます。" : "",
    !editing && pending.length
      ? `この絵に置きたいと書いてある語が ${pending.length} 語あります（「置く」から）。` : "",
    unchecked ? `チェックを外した ${unchecked} 語は出していません。` : "",
    noCoords ? `座標が書かれていない ${noCoords} 語は出していません。` : "",
    elsewhere ? `別の絵にいる ${elsewhere} 語は出していません。` : "",
    offEdges ? `片端が地図に無い関係を ${offEdges} 本伏せています。` : "",
    others.length ? `ほかに ${others.map((m) => `「${m.name}」`).join("")} があります。` : "",
    all.size ? "" : "この絵に置かれた語がありません。",
    all.size && !pos.size ? "すべてチェックが外れています。" : "",
  ].filter(Boolean).join("");

  return {
    root, box, lonely: 0, tucked: tuckedEdges.size, note,
    map: `${chosen.scope}/${chosen.name}`,
    // 呼ぶ側がチェックの一覧を作れるように、**この絵に置ける語**を全部返す
    items,
    // まだ置いていない語（絵の名前だけ書いてある）。置く動線はこれで作る
    pending,
  };
}


//: 形ごとの最小の点数。**サーバ側 (`models._clean_points`) と同じ値**で、
//: 割ると**丸ごと空になる**（半端な形を描かないため）。ここで先に断るのは、
//: 「消したら地図から消えた」を起こさないため
export const LEAST = { point: 1, line: 2, area: 3 };

//: 画面に出す種別の名前。**frontmatter の項目名も添える**（そこを直しに行く人が
//: いるので、画面の言葉とファイルの言葉を繋いでおく）
export const KIND_WORDS = { point: "点 (pin)", line: "線 (line)", area: "領域 (area)" };

//: 種別を変えるときに足す点の間隔（絵の幅に対する比）。掴んで動かす前提なので
//: 「重ならずに掴める」だけあればよい
const GROW_STEP = 0.06;

//: 足す向き（`GROW_STEP` の何倍か）。**縦横のどちらかに揃えない。**
//: 真横に並んだ線は**外形の高さが 0** になり、焦点の枠も外形で見る道具も
//: 「大きさの無い部品」として扱う（`graph.js` の `hitBand()` と同じ話）。
//: **3 点目は一直線に置かない** —— 潰れた三角形は面積が 0 で、領域が見えない
const GROW_AT = [[1, 1], [1, 0], [0, 1], [2, 1]];

/**
 * 種別を変えたときの点の並び。**足りなければ足し、多ければ落とす。**
 *
 * **種別は人が宣言する**（書き方が宣言そのもの、という約束）ので、点の数から
 * 機械が推測しない —— ここは「点にすると言われた」あとで形を合わせるだけ。
 * 足す点は**いまある点の並びの続き**に置く（画面の外へ出さないよう畳む）。
 */
export function fitToKind(kind, points) {
  const least = LEAST[kind] || 1;
  const out = (kind === "point" ? points.slice(0, 1) : points).map((p) => [...p]);
  const base = out[0] || [0.5, 0.5];
  // **絵の外に出さない**（出すと点検が「座標が絵の外」として挙げる形になる）。
  // 右端にいるときは左へ伸ばす —— 詰めると同じ座標が並び、長さ 0 の線になる
  const dir = base[0] > 1 - GROW_STEP * 2 ? -1 : 1;
  while (out.length < least) {
    // **最初の点から測る。** 直前の点から測ると同じ向きに伸び続けて、
    // 3 点が一直線に並ぶ（面積 0 の領域になる）
    const [ox, oy] = GROW_AT[(out.length - 1) % GROW_AT.length];
    out.push([base[0] + ox * GROW_STEP * dir, base[1] + oy * GROW_STEP]);
  }
  return out;
}

/** 画面の座標を絵の座標へ。**`getScreenCTM()` の逆行列**で拡大縮小と移動を吸収する。 */
function toUser(root, ev) {
  const ctm = root.getScreenCTM();
  if (!ctm) return null;
  const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

/**
 * 頂点を掴んで動かせるようにする。**地図だけの例外。**
 *
 * 相関図で「ノードを掴んで動かす」を捨てた理由は**「座標を書く場所が無い」**
 * だった（→ docs/design-notes.md）。地図はまさにその場所を作る図なので、
 * ここでは正当化される —— **段の図や他の見せ方へ広げないこと。**
 *
 * **掴みは動き出してから `setPointerCapture` する。** `pointerdown` で捕まえると
 * 以後のポインタ事象が付け替えられ、**中の線を押しても編集ダイアログが開かなくなる**
 * （拡大縮小の掴みで実際に踏んだ）。
 *
 * 頂点は**足せる・消せる**。守ること 3 つ:
 *
 * - **足す口は線分の中点に置く**（線そのものは押せない —— 形は `<a>` の中にあり、
 *   押すと辞書ページへ飛ぶ。当たり判定を奪うと**線を押して語へ行けなくなる**）
 * - **消す口は乗せたときだけ出す**（✕）。常に出すと、頂点の数だけ ✕ が並んで
 *   絵が読めなくなるうえ、**掴もうとして消す**
 * - **最小を割る削除は断る**（`LEAST`）。サーバ側は割れた形を**丸ごと空にする**
 *   ので、通すと「1 つ消したら地図から消えた」になる。**種別を勝手に落とさない**
 *   のも同じ理由 —— 領域を線に変えるのは人が宣言することであって、消した結果
 *   そうなるものではない
 */
function installHandles(root, pos, onMove, onRefuse) {
  const layer = svg("g", { class: "rel-map-handles" });
  //: いまの点の並びを比に戻す（保存はいつもこの形で渡す）
  const asRatio = (pts) => pts.map((s) => [s.x / W, s.y / W]);

  for (const [ref, p] of pos) {
    const term = p.node.term || ref;
    const save = () => onMove?.(ref, p.kind, asRatio(p.pts));
    const drop = (i) => {
      if (p.pts.length - 1 < (LEAST[p.kind] || 1)) {
        onRefuse?.(
          `${KIND_WORDS[p.kind]}は ${LEAST[p.kind]} 点からです`
          + `（これ以上消すなら種別を変えてください）`
        );
        return;
      }
      p.pts.splice(i, 1);
      save();
    };
    const insert = (i, at) => {
      p.pts.splice(i, 0, at);
      save();
    };
    /**
     * キーボードで足すとき、どの線分に入れるか。
     *
     * **点には足せない**（足すと種別が変わる ＝ 人が宣言することを機械が決める）。
     * 線の**最後の頂点**には「次」が無いので手前の線分へ入れる。領域は閉じて
     * いるので必ず「次」がある。
     */
    const addAfter = (i) => {
      if (p.kind === "point") {
        onRefuse?.("点には頂点を足せません（種別を線か領域に変えてください）");
        return;
      }
      const hasNext = p.kind === "area" || i + 1 < p.pts.length;
      const a = hasNext ? i : i - 1;
      const b = hasNext ? (i + 1) % p.pts.length : i;
      insert(a + 1, midOf(p.pts[a], p.pts[b]));
    };

    p.pts.forEach((q, i) => {
      const dot = svg("circle", {
        class: "rel-map-handle", cx: q.x, cy: q.y, r: 9,
        tabindex: "0", role: "button",
        "aria-label": `${term} の位置を動かす`,
      });
      dot.append(svg("title", {
        text: `${term}（掴んで動かせます。＋ で足す / Delete で消す）`,
      }));
      let moved = false;
      const down = (ev) => {
        if (ev.button !== 0) return;
        // **図全体の移動と取り合わない。** 掴んでいる間は親へ渡さない
        ev.stopPropagation();
        moved = false;
        const move = (e2) => {
          const at = toUser(root, e2);
          if (!at) return;
          moved = true;
          p.pts[i] = at;
          dot.setAttribute("cx", at.x);
          dot.setAttribute("cy", at.y);
          redraw(p);
        };
        const up = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          if (moved) save();
        };
        // **window で受ける。** 丸に付けると、指が丸から出た時点で届かなくなる
        // （実際に踏んだ）。`setPointerCapture` は使わない —— 拡大縮小の掴みで
        // 「中の線を押しても click が届かない」を踏んだのと同じ道具なので、
        // **要らないなら持ち出さない**
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      };
      dot.addEventListener("pointerdown", down);
      // キーボードでも動かせる（矢印。**掴めない人を締め出さない**）。
      // 足す・消すも同じ —— 中点の丸は焦点を持たないので、ここが唯一の道
      dot.addEventListener("keydown", (ev) => {
        if (ev.key === "Delete" || ev.key === "Backspace") {
          ev.preventDefault();
          ev.stopPropagation();
          drop(i);
          return;
        }
        if (ev.key === "+" || ev.key === "Insert") {
          ev.preventDefault();
          ev.stopPropagation();
          addAfter(i);
          return;
        }
        const step = ev.shiftKey ? 20 : 4;
        const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
          ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key];
        if (!d) return;
        ev.preventDefault();
        ev.stopPropagation();
        p.pts[i] = { x: p.pts[i].x + d[0], y: p.pts[i].y + d[1] };
        dot.setAttribute("cx", p.pts[i].x);
        dot.setAttribute("cy", p.pts[i].y);
        redraw(p);
        save();
      });

      // **消す口は乗せたときだけ。** 掴む丸とは別の部品にして、掴みに巻き込まれない
      const cross = svg("g", { class: "rel-map-drop", role: "button", tabindex: "-1" }, [
        svg("circle", { cx: q.x + 13, cy: q.y - 13, r: 7 }),
        svg("path", {
          d: `M ${q.x + 10} ${q.y - 16} L ${q.x + 16} ${q.y - 10}`
            + ` M ${q.x + 16} ${q.y - 16} L ${q.x + 10} ${q.y - 10}`,
        }),
        svg("title", { text: `${term} のこの頂点を消す` }),
      ]);
      cross.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      cross.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        drop(i);
      });

      layer.append(svg("g", { class: "rel-map-vertex" }, [dot, cross]));
    });

    // **足す口は線分の中点。** 線そのものは辞書ページへの入口なので奪えない
    if (p.kind !== "point") {
      const last = p.kind === "area" ? p.pts.length : p.pts.length - 1;
      for (let i = 0; i < last; i++) {
        const at = midOf(p.pts[i], p.pts[(i + 1) % p.pts.length]);
        const add = svg("circle", {
          class: "rel-map-add", cx: at.x, cy: at.y, r: 6,
          role: "button",
          // **焦点は持たせない。** 頂点だけでも Tab の停留が点の数だけあるので、
          // 中点まで並べると図を横切るのに倍かかる（キーボードは ＋ で足せる）
          "aria-hidden": "true",
        });
        add.append(svg("title", { text: `${term} にここで頂点を足す` }));
        add.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        add.addEventListener("click", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          insert(i + 1, at);
        });
        layer.append(add);
      }
    }
  }
  root.append(layer);
}

/** 2 点の中間。**足す口の位置**（＝足したときの座標）。 */
function midOf(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/** 動かしている最中の見た目を追従させる（保存は離したとき）。 */
function redraw(p) {
  const points = asPoints(p.pts);
  for (const node of p.group?.querySelectorAll("polygon, polyline") || []) {
    node.setAttribute("points", points);
  }
  const dot = p.group?.querySelector("circle");
  if (dot && p.kind === "point") {
    dot.setAttribute("cx", p.pts[0].x);
    dot.setAttribute("cy", p.pts[0].y);
  }
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
