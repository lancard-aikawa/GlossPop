// 時系列の見せ方。**縦軸が読み進む向き**（上が先）で、関係を「その文書のどこで
// 読めるようになるか」の順に並べる。「第一章では吾輩と主人だけ、第六章で金田家が
// 繋がる」を読ませるための図。
//
// **この見せ方だけができること: 「いつ」が見える。** 他の 3 つは辞書を平らに
// 出すので、どの関係が先に読めるようになるのかは絵に出ない。ネタバレ抑止を
// 一級の概念にしているこの辞書では、そこがいちばん知りたい軸になる。
//
// **位置はサーバが計算して渡す**（`timeline.py` の `at` / `at_label`）。
// 保存はしないので、本文を書き換えれば次に開いたときの図が変わるだけ。
// 順序は計算値 (`at`)、帯の見出しは表示用の文字列 (`at_label`) で、
// **人が書いた `reveal` は上書きしない**（人の言葉は人の言葉のまま出す）。
//
// 読むものが決まっていないと時系列は定義できないので、**この見せ方は `?doc=`
// のときだけ**（呼ぶ側が面倒を見る。→ graph.js）。
import {
  describeNode, describeRelation, estTextWidth, relationWords, svgEl as svg,
} from "./base.js";
import { splitLonely, wrapLonely } from "./graph-model.js";

const PAD = 16;
const ROW_H = 46;            // 関係 1 本ぶんの高さ
const BAND_GAP = 16;         // 帯と帯のあいだに足す間
const NODE_H = 30;
const NODE_FONT = 12;
const NODE_PAD = 22;         // 箱の中の余白（文字幅に足す）
const NODE_MIN_W = 64;
const NODE_MAX_W = 190;
const AXIS_GAP = 10;         // 見出しと軸のあいだ
const STUB = 20;             // 軸と最初の箱のあいだ
const LINE_MIN = 64;         // 一言が無くても、矢印が読める長さは要る
const LONELY_GAP = 34;
const ROW_H_LONELY = 26;

//: 帯の見出しと一言に立てる字数の上限。切ったことは「…」で分かるし、
//: 全文は図の下の枠（`data-detail`）とブラウザの吹き出しで読める
const HEAD_MAX = 14;
const WORDS_MAX = 14;

//: 位置を出せなかった関係を入れる帯。**黙って落とさない**ための場所で、
//: 読む順の図では普通は空（両端が出てくる語だけが図に載っているため）。
//: **軸によって言うことが違う** —— 作中の時刻では「まだ書かれていない」が普通
const UNDATED = { read: "位置が分からない", when: "時刻が分からない" };

//: 並べる軸。**2 つあり、どちらで並べているかは必ず画面に書く**（→ graph.js）。
//: read = 読者がその文書のどこで読めるようになるか（サーバが計算する `at`）、
//: when = 作中でいつ起きたか（人が書いた `when` の先頭の西暦）。
//: **混ぜないこと** —— 同じ縦軸に 2 つの時間を並べると、読み手にはどちらの順で
//: 並んでいるのか分からない（そもそも一致しないから 2 つある）
const AXES = {
  read: { at: "at", label: "at_label", words: "読む順" },
  when: { at: "when_at", label: "when", words: "作中の時刻" },
};

function clip(text, max) {
  const chars = [...String(text || "")];
  return chars.length > max ? `${chars.slice(0, max).join("")}…` : chars.join("");
}

function nodeWidth(term) {
  return Math.max(NODE_MIN_W, Math.min(NODE_MAX_W, estTextWidth(term, NODE_FONT) + NODE_PAD));
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
 * 関係を「読めるようになる順」に並べ、同じ位置のものを帯にまとめる。
 *
 * **位置の出せない関係は最後の帯に入れる。** 落とすと、図が黙って欠ける
 * （伏せた本数を必ず返すのと同じ約束）。並べ替えが同点のときはサーバが返した
 * 順で決める —— 乱数も時刻も混ぜないので、同じ辞書なら毎回同じ絵になる。
 */
function bandsOf(edges, axis) {
  const { at, label: labelKey } = AXES[axis] || AXES.read;
  const dated = [];
  const undated = [];
  edges.forEach((edge, i) => {
    (typeof edge[at] === "number" ? dated : undated).push(i);
  });
  dated.sort((x, y) => edges[x][at] - edges[y][at] || x - y);

  const bands = [];
  for (const i of dated) {
    const label = edges[i][labelKey] || "";
    const last = bands[bands.length - 1];
    // 位置は増える一方なので、同じ見出しは必ず続きになる（飛び地はできない）。
    // **束ねるのは「並べ替えの値も見出しも同じ」ものだけ。** 作中の時刻は人が
    // 書くので、同じ時刻を 2 通りに書けてしまう（`1560-06-12` と
    // `1560-06-12 昼ごろ`）—— こちらで片方に寄せると、**書いていない文字列が
    // 見出しに出る**（人の言葉を置き換えない、という約束のほうを採る）。
    // 読む順の軸では位置から見出しを作るので、この枝はそもそも効かない
    if (last && last.label === label && last.at === edges[i][at]) last.rows.push(i);
    else bands.push({ label, at: edges[i][at], rows: [i] });
  }
  if (undated.length) {
    bands.push({ label: UNDATED[axis] || UNDATED.read, rows: undated, undated: true });
  }
  return bands;
}

/**
 * 時系列を組み立てる。返すのは他の見せ方と同じ ``{ root, box }``
 * （拡大縮小と移動、下の枠は呼ぶ側が同じ仕組みで面倒を見る）。
 *
 * @param {object} graph  `/api/graph?doc=…` の返り値
 * @param {function} onEdge 関係を押したときに呼ぶ（編集ダイアログ）
 * @param {string} axis  並べる軸（`read` = 読む順 / `when` = 作中の時刻）
 */
export function buildTimeline(graph, { onEdge, axis: wanted = "read" } = {}) {
  const { nodes, edges } = graph;
  // **受け取り側で名前を変える。** この関数の中では `axis` を軸の線（`<g>`）に
  // 使っている —— 同じ名前で 2 つ宣言すると読み込みごと落ちる（`labels` で
  // 一度踏んだのと同じ形）
  const on = AXES[wanted] ? wanted : "read";
  // 関係の無い語は行にしない（他の見せ方と同じく、下の帯へまとめる）
  const { lonely } = splitLonely(nodes, edges);
  const termOf = new Map(nodes.map((n) => [n.ref, n]));
  const bands = bandsOf(edges, on);

  // 列の幅は全体で揃える。行ごとに変えると、同じものを縦に読めない
  const rows = bands.flatMap((b) => b.rows);
  const widthOfSide = (pick) => Math.max(
    NODE_MIN_W,
    ...rows.map((i) => nodeWidth(termOf.get(edges[i][pick])?.term || edges[i][pick])),
  );
  const fromW = widthOfSide("from");
  const toW = widthOfSide("to");
  const lineW = Math.max(
    LINE_MIN,
    ...rows.map((i) => estTextWidth(clip(relationWords(edges[i]), WORDS_MAX), 11) + 16),
  );
  const headW = Math.max(
    40, ...bands.map((b) => estTextWidth(clip(b.label, HEAD_MAX), 11)),
  );
  const revealW = Math.max(
    0, ...rows.map((i) => (edges[i].reveal
      ? estTextWidth(`判明: ${clip(edges[i].reveal, HEAD_MAX)}`, 11) + 10 : 0)),
  );

  const axisX = PAD + headW + AXIS_GAP;
  const x0 = axisX + STUB;
  const width = x0 + fromW + lineW + toW + revealW + PAD;

  // 行の y をあらかじめ決める（帯の見出しは、その帯の最初の行に並べる）
  let y = PAD + ROW_H / 2;
  const placed = [];
  for (const band of bands) {
    band.top = y - ROW_H / 2;
    band.y = y;
    for (const i of band.rows) {
      placed.push({ edge: edges[i], y });
      y += ROW_H;
    }
    y += BAND_GAP;
  }
  const bodyBottom = placed.length ? y - BAND_GAP - ROW_H / 2 + NODE_H / 2 : PAD;

  const strip = wrapLonely(lonely, {
    width, top: bodyBottom, pad: PAD, rowHeight: ROW_H_LONELY, gap: LONELY_GAP,
    widthOf: (node) => estTextWidth(node.term, NODE_FONT) + 28,
  });
  const height = strip.bottom + PAD;

  const root = svg("svg", {
    class: "rel-graph rel-timeline",
    width: "100%",
    height: "100%",
    viewBox: `0 0 ${Math.ceil(width)} ${Math.ceil(height)}`,
    role: "img",
    "aria-label": "用語の相関図（時系列の見せ方）",
  });
  root.append(svg("defs", {}, [marker("tl-arrow", "rel-arrowhead")]));

  // 軸と切れ目は飾り。**当たり判定を持たせない**（行の上に乗ると押せなくなる）
  const axis = svg("g", { class: "tl-axis" });
  if (placed.length) {
    axis.append(svg("line", { x1: axisX, y1: PAD, x2: axisX, y2: bodyBottom }));
  }
  bands.forEach((band, i) => {
    if (i) {
      axis.append(svg("line", {
        class: "tl-split", x1: PAD, y1: band.top - BAND_GAP / 2,
        x2: Math.max(width - PAD, PAD + 40), y2: band.top - BAND_GAP / 2,
      }));
    }
    axis.append(svg("circle", { class: "tl-tick", cx: axisX, cy: band.y, r: 3.5 }));
  });
  root.append(axis);

  // 帯の見出しは**軸とは別**に置く。飾りは押せなくしてあるが、見出しは
  // 乗せたら下の枠に出したい（切った全文がそこでしか読めない）
  const heads = svg("g", { class: "tl-heads" });
  for (const band of bands) {
    // **軸によって言うことが違う。** 同じ帯を「ここで読める」と「このとき起きた」の
    // どちらにも読めてしまうと、2 つの時間を混ぜたのと同じことになる
    const detail = band.undated
      ? (on === "when"
        ? `${band.label} — 作中の時刻が書かれていないか、西暦として読めません（${band.rows.length} 本）`
        : `${band.label} — この文書のどこで読めるようになるか分かりません（${band.rows.length} 本）`)
      : (on === "when"
        ? `${band.label} — このとき（作中）の関係 ${band.rows.length} 本`
        : `${band.label} — ここで読めるようになる関係 ${band.rows.length} 本`);
    heads.append(svg("g", {
      class: band.undated ? "tl-head undated" : "tl-head",
      "data-detail": detail,
    }, [
      svg("text", {
        x: axisX - AXIS_GAP,
        y: band.y,
        "text-anchor": "end",
        "dominant-baseline": "central",
        text: clip(band.label, HEAD_MAX),
      }),
      // **`<text>` の中に入れないこと** —— 描かれないまま文字の内容として
      // 数えられ、見出しを読む側（スモークテストも含む）が別物を読む
      svg("title", { text: detail }),
    ]));
  }
  root.append(heads);

  // 語ごとの箱と、その語に繋がる行。**1 つの語は何度も出てくる**（関係の数だけ
  // 行に現れる）ので、乗せたときの強調は「その ref の箱すべて」を相手にする
  const nodeGroups = new Map(nodes.map((n) => [n.ref, []]));
  const touching = new Map(nodes.map((n) => [n.ref, []]));

  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  const boxes = svg("g", { class: "tl-nodes" });

  for (const { edge, y: rowY } of placed) {
    const from = termOf.get(edge.from);
    const to = termOf.get(edge.to);
    const x1 = x0 + fromW;
    const x2 = x1 + lineW;
    const detail = describeRelation(edge, { from: from?.term, to: to?.term });
    const cls = ["rel-edge", edge.missing ? "missing" : "", edge.reveal ? "reveal" : ""]
      .filter(Boolean).join(" ");
    const group = svg("g", {
      class: "rel-edge-group",
      tabindex: "0",
      role: "button",
      "aria-label": `関係を直す: ${detail}`,
      // 図の下の枠に出す文。**一言は切ることがあるので、全文はここでしか読めない**
      "data-detail": detail,
    }, [
      // **押せる範囲を外形としても持たせる**（`graph.js` の `hitBand()` と同じ）。
      // 外形に線の太さは入らないので、真横の線だけだと**高さ 0 の部品**になり、
      // 押せるのに「大きさが無い」と扱われる（焦点の枠も、外形で見る道具も
      // 見つけられない）。塗らないし、押しても拾わない
      svg("rect", {
        class: "rel-edge-band",
        x: x1, y: rowY - 7, width: Math.max(x2 - x1, 1), height: 14,
      }),
      // 線は細くて押せない。透明な太い線を重ねて当たり判定にする（他の見せ方と同じ）
      svg("line", { x1, y1: rowY, x2, y2: rowY, class: "rel-edge-hit" }),
      svg("line", {
        x1, y1: rowY, x2, y2: rowY,
        class: cls,
        "marker-end": "url(#tl-arrow)",
        // 相互なら両端に矢印。一方的なら向いている側だけ（他の見せ方と同じ約束）
        "marker-start": edge.mutual ? "url(#tl-arrow)" : null,
      }),
    ]);
    group.append(svg("title", { text: `${detail}（押すと直せます）` }));

    const words = relationWords(edge);
    const text = words
      ? svg("text", {
        class: "rel-edge-label",
        x: (x1 + x2) / 2,
        y: rowY - 9,
        "text-anchor": "middle",
        "data-detail": detail,
        text: clip(words, WORDS_MAX),
      })
      : null;
    // **人が書いた判明位置はそのまま出す。** 並べ替えはこちらの計算値でやるが、
    // 書かれている言葉を計算した位置で置き換えたりはしない
    const said = edge.reveal
      ? svg("text", {
        class: "tl-reveal",
        x: x2 + toW + 8,
        y: rowY,
        "dominant-baseline": "central",
        "data-detail": detail,
        text: `判明: ${clip(edge.reveal, HEAD_MAX)}`,
      })
      : null;
    lines.append(group);
    for (const node of [text, said]) if (node) labels.append(node);

    // **1 本の関係のものは一緒に光らせる**（線・一言・判明位置は別の層に居るので
    // CSS の子孫セレクタでは届かない）。片方だけ光ると、どの関係なのか分からない
    const parts = [group, text, said].filter(Boolean);
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

    const box = (node, ref, left, w) => {
      const term = node?.term || ref;
      const cell = svg("g", {
        class: ["rel-node", node?.missing ? "missing" : "", node?.outside ? "outside" : ""]
          .filter(Boolean).join(" "),
        "data-detail": node ? describeNode(node) : term,
      }, [
        svg("a", { href: node?.url || `/glossary?q=${encodeURIComponent(term)}` }, [
          svg("rect", { x: left, y: rowY - NODE_H / 2, width: w, height: NODE_H, rx: 8 }),
          svg("text", {
            x: left + w / 2, y: rowY, "text-anchor": "middle",
            "dominant-baseline": "central", text: clip(term, HEAD_MAX),
          }),
          svg("title", {
            text: node?.missing
              ? `${term} — 未登録（押すと辞書で探せます）`
              : [term, node?.path_label, node?.summary].filter(Boolean).join(" — "),
          }),
        ]),
      ]);
      boxes.append(cell);
      nodeGroups.get(ref)?.push(cell);
      return cell;
    };
    box(from, edge.from, x0, fromW);
    box(to, edge.to, x2, toW);

    for (const ref of [edge.from, edge.to]) {
      touching.get(ref)?.push({ parts, other: ref === edge.from ? edge.to : edge.from });
    }
  }
  root.append(lines, boxes, labels);

  if (!placed.length) {
    root.append(svg("text", {
      class: "tl-empty",
      x: PAD,
      y: PAD + 12,
      text: on === "when"
        ? "作中の時刻が書かれた関係がありません。"
        : "この文書では、まだ関係が読める組がありません。",
    }));
  }

  // 関係の無い語。**この文書に出てくる語ではある**ので消さない（他の見せ方と同じ）
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
    for (const { node, x, y: cy } of strip.cells) {
      root.append(svg("g", { class: "rel-node", "data-detail": describeNode(node) }, [
        svg("a", { href: node.url }, [
          svg("text", {
            x, y: cy, "text-anchor": "middle", "dominant-baseline": "central", text: node.term,
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
    note: axisNote(on, bands, edges),
  };
}

/**
 * 凡例に足す文。**どちらの軸で並べているか**と、**並ばなかった数**を出す。
 *
 * 作中の時刻は**書いていない関係のほうが普通**（全部に時刻が付く辞書のほうが
 * 珍しい）。だから「時刻が分からない」の帯には 2 種類が混ざる:
 *
 * - **まだ書いていない** —— 正常。点検も黙る
 * - **書いたが西暦として読めない** —— 直せる。点検が挙げる
 *
 * **帯は同じでも数は分ける。** まとめて 1 つの数にすると、書き間違いが
 * 「まだ書いていないぶん」に紛れて見えなくなる（黙って欠けさせない、の一種）。
 */
function axisNote(axis, bands, edges) {
  const undated = bands.find((b) => b.undated);
  if (axis !== "when") {
    return undated
      ? `この文書を読み進める順に並べています（位置の分からない ${undated.rows.length} 本は最後）。`
      : "この文書を読み進める順に並べています。";
  }
  if (!undated) return "作中の時刻の順に並べています。";
  const unreadable = undated.rows.filter((i) => (edges[i].when || "").trim()).length;
  const blank = undated.rows.length - unreadable;
  return "作中の時刻の順に並べています。"
    + (blank ? `時刻を書いていない ${blank} 本は最後にまとめています。` : "")
    + (unreadable
      ? `時刻を書いてあるのに西暦として読めない ${unreadable} 本も同じ帯です`
        + "（点検の「時刻が西暦で読めない」で直せます）。"
      : "");
}

/**
 * 1 つの語に乗せている間、その語の関係だけを濃く出す（他の見せ方と同じ作法）。
 *
 * ここだけ違うのは、**同じ語の箱が何個もある**こと（関係の数だけ行に現れる）。
 * 1 つに乗せたら同じ語の箱を全部光らせる —— 縦に離れた同じ語が繋がって見える。
 */
function installFocus(root, nodeGroups, touching) {
  const light = (ref, on) => {
    root.classList.toggle("focusing", on);
    if (!on) {
      for (const node of root.querySelectorAll(".lit")) node.classList.remove("lit");
      return;
    }
    for (const cell of nodeGroups.get(ref) || []) cell.classList.add("lit");
    for (const { parts, other } of touching.get(ref) || []) {
      for (const node of parts) node.classList.add("lit");
      for (const cell of nodeGroups.get(other) || []) cell.classList.add("lit");
    }
  };
  for (const [ref, cells] of nodeGroups) {
    for (const cell of cells) {
      cell.addEventListener("pointerenter", () => light(ref, true));
      cell.addEventListener("pointerleave", () => light(ref, false));
      cell.addEventListener("focusin", () => light(ref, true));
      cell.addEventListener("focusout", () => light(ref, false));
    }
  }
}
