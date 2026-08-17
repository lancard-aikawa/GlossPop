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
  describeNode, describeRelation, estTextWidth, relationWords,
  svgEl as svg, svgVerticalText,
} from "./base.js";
import { isMainRelation, splitLonely, wrapLonely } from "./graph-model.js";

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
function bandsOf(edges, axis, nodeOf) {
  const { at, label: labelKey } = AXES[axis] || AXES.read;
  const dated = [];
  const undated = [];
  edges.forEach((edge, i) => {
    (typeof edge[at] === "number" ? dated : undated).push(i);
  });
  // **同じ時刻の中では、主（両端が同じカテゴリ）を先に。** 事件から伸びる関係には
  // 話の筋（事件 → 事件）と顔ぶれ（事件 → 人物・場所）が混ざっていて、顔ぶれの
  // 多い事件ほど筋が下に埋もれる。**落としはしない**（後ろに回るだけ）
  const main = (i) => (isMainRelation(edges[i], nodeOf) ? 0 : 1);
  dated.sort((x, y) => edges[x][at] - edges[y][at] || main(x) - main(y) || x - y);
  undated.sort((x, y) => main(x) - main(y) || x - y);

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
    // **「だいたい」は帯ごと。** 見出しの文字列が同じものだけを束ねているので、
    // 同じ帯なら書き方も同じ（`16世紀` と `1501` が同じ帯に来ることはない）。
    // **これは作中の時刻の軸の話。** 読む順の帯は本文の位置で束ねているので、
    // `when_about` を持ち込むと**関係に `16世紀` と書いただけで読む順の目盛りが
    // 白抜きになる**（「章の位置がだいたい」という意味に読まれる）—— 軸の判定は
    // ここ 1 か所に置き、目盛りも見出しも凡例もこの値だけを見る
    else bands.push({
      label, at: edges[i][at], rows: [i],
      about: axis === "when" && !!edges[i].when_about,
    });
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
export function buildTimeline(graph, { onEdge, axis: wanted = "read", rows: unit } = {}) {
  // **並べるものが 2 つある。**関係（1 行 1 本）と語（1 行 1 語 ＝ 年表）。
  // 置き換えではない —— 関係の並びは「誰と誰がいつ繋がるか」、語の並びは
  // 「何がいつ起きたか」で、答える問いが違う（→ `buildTermRows`）
  if (unit === "node") return buildTermRows(graph, { onEdge, axis: wanted });
  const { nodes, edges } = graph;
  // **受け取り側で名前を変える。** この関数の中では `axis` を軸の線（`<g>`）に
  // 使っている —— 同じ名前で 2 つ宣言すると読み込みごと落ちる（`labels` で
  // 一度踏んだのと同じ形）
  const on = AXES[wanted] ? wanted : "read";
  // 関係の無い語は行にしない（他の見せ方と同じく、下の帯へまとめる）
  const { lonely } = splitLonely(nodes, edges);
  const termOf = new Map(nodes.map((n) => [n.ref, n]));
  const bands = bandsOf(edges, on, (ref) => termOf.get(ref));

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
    // **だいたいの時刻は目盛りを白抜きにする。** 位置は範囲の頭に置いてあるので、
    // 実線の点にすると `16世紀` が 1501 年ちょうどに見える（読んだ値で人の言葉を
    // 置き換えないのと同じ理由 —— 精度まで置き換えない）
    axis.append(svg("circle", {
      class: band.about ? "tl-tick about" : "tl-tick", cx: axisX, cy: band.y, r: 3.5,
    }));
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
          + (band.about ? "（だいたいの時刻。範囲の頭に置いています）" : "")
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
  const about = bands.some((b) => b.about);
  const aboutNote = about
    ? "だいたいの時刻（16世紀・約1560・1560ごろ）は、白抜きの目盛りでその範囲の頭に置いています。"
    : "";
  if (!undated) return `作中の時刻の順に並べています。${aboutNote}`;
  const unreadable = undated.rows.filter((i) => (edges[i].when || "").trim()).length;
  const blank = undated.rows.length - unreadable;
  return "作中の時刻の順に並べています。"
    + aboutNote
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

// --------------------------------------------------------------------------- //
// 年表（1 行 1 語）
//
// **関係の並びとは答える問いが違う。** あちらは「誰と誰がいつ繋がるか」、
// こちらは「何がいつ起きたか」。事件を順に読むには、関係を 1 本ずつ並べた図では
// 同じ事件が関係の本数だけ現れてしまう（本能寺の変は 8 行に散る）。
//
// **主と従は、軸に載るかどうかで決まる。** 位置のある語（＝事件・出来事）が行に
// なり、位置の無い相手（＝人物・場所）はその行に**従のチップ**として付く。
// カテゴリで「これは人物」と決めつける必要は無い —— **人物は期間なので時刻を
// 書かない**（→ MANUAL）ぶん、自然に従へ落ちる。カテゴリで絞れば、行になる語を
// そのカテゴリだけに狭められる（範囲外の語は `outside` として来るので行にしない）。
// --------------------------------------------------------------------------- //

const TERM_H = 34;           // 語 1 行ぶんの高さ
const TERM_ROW_H = 44;
const CHIP_H = 20;
const CHIP_GAP = 6;
const CHIP_PAD = 14;
const CHIP_MAX = 6;          // 1 行に出す従の数。超えたぶんは「ほか N」にまとめる
const ARC_COL = 20;          // 弧 1 本ぶんの列幅
const ARC_GAP = 12;          // 従の右端と最初の列のあいだ
const ARC_LINE_H = 11;       // 縦書きの一言、1 字ぶんの高さ
const ARC_LABEL_GAP = 8;     // 弧の下側の端点と一言の下端のあいだ
const ARC_WORDS_MAX = 10;    // 弧の一言に立てる字数の上限（弧が短ければさらに切る）

/** 語 1 つの、その軸での位置。**行になれるのは数を持つものだけ。** */
function nodeAt(node, on) {
  const value = node[AXES[on].at];
  return typeof value === "number" ? value : null;
}

/** 弧に列を割り当てる。**長いものを外側へ**（短い弧が内側に収まって重ならない）。 */
function arcColumns(spans) {
  const order = spans.map((_, i) => i).sort((x, y) => {
    const a = spans[x];
    const b = spans[y];
    return (b.hi - b.lo) - (a.hi - a.lo) || a.lo - b.lo;
  });
  const used = [];                       // 列ごとの [lo, hi] の並び
  const column = new Array(spans.length).fill(0);
  for (const i of order) {
    const { lo, hi } = spans[i];
    let col = 0;
    // **重なりを許さない。** 同じ列に重ねると 2 本が 1 本に見える
    while (used[col] && used[col].some((s) => lo < s.hi && s.lo < hi)) col += 1;
    if (!used[col]) used[col] = [];
    used[col].push({ lo, hi });
    column[i] = col;
  }
  return { column, count: Math.max(used.length, 0) };
}

function buildTermRows(graph, { onEdge, axis: wanted = "read" } = {}) {
  const { nodes, edges } = graph;
  const on = AXES[wanted] ? wanted : "read";
  const termOf = new Map(nodes.map((n) => [n.ref, n]));

  // **行になるのは「位置があって、範囲の中にいる語」だけ。** `outside` は
  // カテゴリで絞ったときに辺の相手として足された語なので、行にすると絞った
  // 意味が無くなる（`build_graph(only=…)` が相手を足さないのと同じ話）
  const rowNodes = nodes
    .filter((n) => !n.outside && !n.missing && nodeAt(n, on) !== null)
    .sort((a, b) => nodeAt(a, on) - nodeAt(b, on)
      || (a[AXES[on].label] || "").localeCompare(b[AXES[on].label] || "", "ja")
      || a.term.localeCompare(b.term, "ja"));
  const indexOf = new Map(rowNodes.map((n, i) => [n.ref, i]));

  // 帯にまとめる。**束ねるのは「並べ替えの値も見出しも同じ」ものだけ**
  // （関係の並びの `bandsOf` と同じ規則 —— 人は同じ日を 2 通りに書ける）
  const bands = [];
  rowNodes.forEach((node, i) => {
    const label = node[AXES[on].label] || "";
    const at = nodeAt(node, on);
    const last = bands[bands.length - 1];
    if (last && last.label === label && last.at === at) last.rows.push(i);
    // **「だいたい」は作中の時刻の軸だけ**（`bandsOf` と同じ判断）。読む順の帯は
    // 本文の位置で束ねているので、語に `16世紀` と書いてあることは何も言わない
    else bands.push({ label, at, rows: [i], about: on === "when" && !!node.when_about });
  });

  // 辺を 3 つに仕分ける: 行どうし（弧）/ 行と従（チップ）/ どちらも行でない（数だけ）
  const arcs = [];
  const chipsOf = rowNodes.map(() => []);
  const seenChip = rowNodes.map(() => new Set());
  let offAxis = 0;
  for (const edge of edges) {
    const a = indexOf.get(edge.from);
    const b = indexOf.get(edge.to);
    if (a !== undefined && b !== undefined) {
      if (a !== b) arcs.push({ edge, lo: Math.min(a, b), hi: Math.max(a, b) });
      continue;
    }
    const row = a !== undefined ? a : b;
    if (row === undefined) {
      // どちらの端も軸に載っていない関係（人物どうしなど）。**数えて凡例に出す**
      offAxis += 1;
      continue;
    }
    const otherRef = a !== undefined ? edge.to : edge.from;
    if (seenChip[row].has(otherRef)) continue;
    seenChip[row].add(otherRef);
    chipsOf[row].push({ node: termOf.get(otherRef), ref: otherRef, edge });
  }

  const { column, count: arcCols } = arcColumns(arcs);

  // 幅。列は全体で揃える（行ごとに変えると縦に読めない）
  const headW = Math.max(40, ...bands.map((b) => estTextWidth(clip(b.label, HEAD_MAX), 11)));
  const termW = Math.max(NODE_MIN_W, ...rowNodes.map((n) => nodeWidth(n.term)));
  const chipW = (text) => estTextWidth(text, 11) + CHIP_PAD;
  const chipsWidth = (list) => {
    const shown = list.slice(0, CHIP_MAX);
    const extra = list.length - shown.length;
    return shown.reduce((w, c) => w + chipW((c.node && c.node.term) || c.ref) + CHIP_GAP, 0)
      + (extra ? chipW(`ほか ${extra}`) + CHIP_GAP : 0);
  };
  const chipsW = Math.max(0, ...chipsOf.map(chipsWidth));

  const axisX = PAD + headW + AXIS_GAP;
  const x0 = axisX + STUB;
  const chipsX = x0 + termW + CHIP_GAP * 2;
  const gutterX = chipsX + chipsW + ARC_GAP;
  const width = gutterX + arcCols * ARC_COL + PAD;

  // y を決める
  const yOf = new Array(rowNodes.length).fill(0);
  let y = PAD + TERM_ROW_H / 2;
  for (const band of bands) {
    band.top = y - TERM_ROW_H / 2;
    band.y = y;
    for (const i of band.rows) {
      yOf[i] = y;
      y += TERM_ROW_H;
    }
    y += BAND_GAP;
  }
  const bodyBottom = rowNodes.length ? y - BAND_GAP - TERM_ROW_H / 2 + TERM_H / 2 : PAD;

  // **行にも従にもならなかった語。** 消さずに帯へ出す（他の見せ方と同じ約束）
  const placedRefs = new Set(rowNodes.map((n) => n.ref));
  for (const seen of seenChip) for (const ref of seen) placedRefs.add(ref);
  const rest = nodes.filter((n) => !placedRefs.has(n.ref) && !n.missing);
  const strip = wrapLonely(rest, {
    width, top: bodyBottom, pad: PAD, rowHeight: ROW_H_LONELY, gap: LONELY_GAP,
    widthOf: (node) => estTextWidth(node.term, NODE_FONT) + 28,
  });
  const height = strip.bottom + PAD;

  const root = svg("svg", {
    class: "rel-graph rel-timeline rel-chronicle",
    width: "100%",
    height: "100%",
    viewBox: `0 0 ${Math.ceil(width)} ${Math.ceil(height)}`,
    role: "img",
    "aria-label": "用語の相関図（年表の見せ方）",
  });
  root.append(svg("defs", {}, [marker("tl-arrow2", "rel-arrowhead")]));

  const axis = svg("g", { class: "tl-axis" });
  if (rowNodes.length) {
    axis.append(svg("line", { x1: axisX, y1: PAD, x2: axisX, y2: bodyBottom }));
  }
  bands.forEach((band, i) => {
    if (i) {
      axis.append(svg("line", {
        class: "tl-split", x1: PAD, y1: band.top - BAND_GAP / 2,
        x2: Math.max(width - PAD, PAD + 40), y2: band.top - BAND_GAP / 2,
      }));
    }
    // **だいたいの時刻は目盛りを白抜きにする。** 位置は範囲の頭に置いてあるので、
    // 実線の点にすると `16世紀` が 1501 年ちょうどに見える（読んだ値で人の言葉を
    // 置き換えないのと同じ理由 —— 精度まで置き換えない）
    axis.append(svg("circle", {
      class: band.about ? "tl-tick about" : "tl-tick", cx: axisX, cy: band.y, r: 3.5,
    }));
  });
  root.append(axis);

  const heads = svg("g", { class: "tl-heads" });
  for (const band of bands) {
    // **軸によって言うことが違う**（関係の並びと同じ理由）
    const detail = (on === "when"
      ? `${band.label} — このとき（作中）の語 ${band.rows.length} 件`
      : `${band.label} — ここで出てくる語 ${band.rows.length} 件`)
      + (band.about ? "（だいたいの時刻。範囲の頭に置いています）" : "");
    heads.append(svg("g", { class: "tl-head", "data-detail": detail }, [
      svg("text", {
        x: axisX - AXIS_GAP, y: band.y, "text-anchor": "end",
        "dominant-baseline": "central", text: clip(band.label, HEAD_MAX),
      }),
      svg("title", { text: detail }),
    ]));
  }
  root.append(heads);

  const lines = svg("g", { class: "rel-edge-lines" });
  const labels = svg("g", { class: "rel-edge-labels" });
  const boxes = svg("g", { class: "tl-nodes" });
  const nodeGroups = new Map(nodes.map((n) => [n.ref, []]));
  const touching = new Map(nodes.map((n) => [n.ref, []]));

  // 弧（行どうしの関係）。**線は細くて押せない**ので当たり判定を重ねる（他と同じ）
  arcs.forEach((arc, i) => {
    const x = gutterX + column[i] * ARC_COL + ARC_COL / 2;
    const yA = yOf[indexOf.get(arc.edge.from)];
    const yB = yOf[indexOf.get(arc.edge.to)];
    const from = termOf.get(arc.edge.from);
    const to = termOf.get(arc.edge.to);
    const detail = describeRelation(arc.edge, {
      from: from && from.term, to: to && to.term,
    });
    const d = `M ${chipsX - CHIP_GAP} ${yA} H ${x} V ${yB} H ${chipsX - CHIP_GAP}`;
    const cls = ["rel-edge", arc.edge.reveal ? "reveal" : ""].filter(Boolean).join(" ");
    const group = svg("g", {
      class: "rel-edge-group",
      tabindex: "0",
      role: "button",
      "aria-label": `関係を直す: ${detail}`,
      "data-detail": detail,
    }, [
      svg("path", { d, class: "rel-edge-hit", fill: "none" }),
      svg("path", {
        d, class: cls, fill: "none",
        "marker-end": "url(#tl-arrow2)",
        "marker-start": arc.edge.mutual ? "url(#tl-arrow2)" : null,
      }),
      svg("title", { text: `${detail}（押すと直せます）` }),
    ]);

    // **一言は縦書き**（列幅 20px に横書きは入らない）。`writing-mode` を
    // 使わないのは `⇄` が回されるから（→ base.js）。
    //
    // **弧の範囲の中に収める。** 下端を弧の下側の端点に揃えて上へ積むのは
    // 変わらないが、弧の長さを見ずに積むと 10 字で 100px 伸びる —— 隣り合う行を
    // 結ぶ弧（44px）では**上の行を飛び越えて枠の外（負の y）へ出る**。外形は
    // `{x:0,y:0,w,h}` なので、出たぶんは `fitView` にも書き出しにも入らない
    // （画面からも渡した相手からも消える）。範囲の中に収めておけば、**同じ列の弧は
    // 範囲が重ならない**（`arcColumns`）ぶん一言どうしも重ならない。切った全文は
    // 下の枠と吹き出しで読める（畳んだ一言と同じ約束）
    const words = relationWords(arc.edge);
    const bottom = Math.max(yA, yB) - ARC_LABEL_GAP;
    const room = Math.floor((bottom - Math.min(yA, yB)) / ARC_LINE_H);
    const text = words && room >= 1
      ? svgVerticalText(words, x, bottom, {
        max: Math.min(ARC_WORDS_MAX, room),
        className: "rel-edge-label",
        lineHeight: ARC_LINE_H,
      })
      : null;
    if (text) text.setAttribute("data-detail", detail);

    lines.append(group);
    if (text) labels.append(text);

    // **線と一言は一緒に光らせる**（別の層に居るので CSS では届かない）
    const parts = [group, text].filter(Boolean);
    const light = (hot) => {
      for (const node of parts) node.classList.toggle("hot", hot);
    };
    const open = () => { if (onEdge) onEdge(arc.edge); };
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
    for (const ref of [arc.edge.from, arc.edge.to]) {
      const list = touching.get(ref);
      if (list) list.push({ parts, other: ref === arc.edge.from ? arc.edge.to : arc.edge.from });
    }
  });

  // 語の箱と、その行に付く従
  rowNodes.forEach((node, i) => {
    const rowY = yOf[i];
    // **`data-ref` を落とさないこと。** 用語ページから「辞書の図で見る →」で
    // 渡ってきたとき、その語の行を光らせる目印にしている（地図と同じ `spotlight()`）
    const cell = svg("g", {
      class: "rel-node", "data-ref": node.ref, "data-detail": describeNode(node),
    }, [
      svg("a", { href: node.url }, [
        svg("rect", { x: x0, y: rowY - TERM_H / 2, width: termW, height: TERM_H, rx: 8 }),
        svg("text", {
          x: x0 + termW / 2, y: rowY, "text-anchor": "middle",
          "dominant-baseline": "central", text: clip(node.term, HEAD_MAX),
        }),
        svg("title", {
          text: [node.term, node.path_label, node.summary].filter(Boolean).join(" — "),
        }),
      ]),
    ]);
    boxes.append(cell);
    const own = nodeGroups.get(node.ref);
    if (own) own.push(cell);

    // 従。**押せばその語の辞書ページへ行ける**（ただの飾りにしない）
    const list = chipsOf[i];
    const shown = list.slice(0, CHIP_MAX);
    const extra = list.length - shown.length;
    let cx = chipsX;
    for (const chip of shown) {
      const term = (chip.node && chip.node.term) || chip.ref;
      const w = chipW(term);
      const detail = describeRelation(chip.edge, {
        from: (termOf.get(chip.edge.from) || {}).term,
        to: (termOf.get(chip.edge.to) || {}).term,
      });
      const box = svg("g", {
        class: ["tl-chip", chip.node && chip.node.missing ? "missing" : ""].filter(Boolean).join(" "),
        "data-detail": detail,
      }, [
        svg("a", {
          href: (chip.node && chip.node.url) || `/glossary?q=${encodeURIComponent(term)}`,
        }, [
          svg("rect", { x: cx, y: rowY - CHIP_H / 2, width: w, height: CHIP_H, rx: 10 }),
          svg("text", {
            x: cx + w / 2, y: rowY, "text-anchor": "middle",
            "dominant-baseline": "central", text: term,
          }),
          svg("title", { text: detail }),
        ]),
      ]);
      boxes.append(box);
      const group = nodeGroups.get(chip.ref);
      if (group) group.push(box);
      // **従も関係の 1 本。** 弧と同じように両端へ登録する —— しないと、
      // 従に乗せたときに `focusing` だけが立って**年表全体が褪せるのに何も
      // 濃くならない**（従はこの関係の唯一の見た目なので、光る先が他に無い）。
      // 行の側に登録するのも同じ理由で、行に乗せたときに顔ぶれが付いてくる
      for (const [ref, other] of [[node.ref, chip.ref], [chip.ref, node.ref]]) {
        const touches = touching.get(ref);
        if (touches) touches.push({ parts: [box], other });
      }
      cx += w + CHIP_GAP;
    }
    if (extra) {
      // **黙って切らない。** 残りは数で出し、全部の名前は乗せたときに読める
      const term = `ほか ${extra}`;
      const w = chipW(term);
      const all = list.slice(CHIP_MAX).map((c) => (c.node && c.node.term) || c.ref).join("、");
      const box = svg("g", { class: "tl-chip more", "data-detail": `${node.term} — ${all}` }, [
        svg("rect", { x: cx, y: rowY - CHIP_H / 2, width: w, height: CHIP_H, rx: 10 }),
        svg("text", {
          x: cx + w / 2, y: rowY, "text-anchor": "middle",
          "dominant-baseline": "central", text: term,
        }),
        svg("title", { text: all }),
      ]);
      boxes.append(box);
      // まとめたぶんも**この行の顔ぶれ**なので、行に乗せたら一緒に濃く出す
      // （光る先の語は 1 つに決まらないので `other` は持たせない）
      const rowTouching = touching.get(node.ref);
      if (rowTouching) rowTouching.push({ parts: [box] });
    }
  });
  root.append(lines, boxes, labels);

  if (!rowNodes.length) {
    root.append(svg("text", {
      class: "tl-empty",
      x: PAD,
      y: PAD + 12,
      text: on === "when"
        ? "作中の時刻が書かれた語がありません（事件や出来事に when を書くと並びます）。"
        : "この文書に出てくる語が見つかりませんでした。",
    }));
  }

  if (rest.length) {
    root.append(svg("g", { class: "rel-lonely-rule" }, [
      svg("line", {
        x1: PAD, y1: strip.ruleY, x2: Math.max(width - PAD, PAD + 40), y2: strip.ruleY,
      }),
      svg("text", {
        x: PAD, y: strip.ruleY - 6, class: "rel-lonely-caption",
        text: on === "when"
          ? `時刻も、時刻のある語との関係も無い語（${rest.length}）`
          : `この文書に出てこない語（${rest.length}）`,
      }),
    ]));
    for (const { node, x, y: cy } of strip.cells) {
      root.append(svg("g", { class: "rel-node", "data-detail": describeNode(node) }, [
        svg("a", { href: node.url }, [
          svg("text", {
            x, y: cy, "text-anchor": "middle", "dominant-baseline": "central", text: node.term,
          }),
          svg("title", { text: [node.path_label, node.summary].filter(Boolean).join(" — ") }),
        ]),
      ]));
    }
  }

  installFocus(root, nodeGroups, touching);
  let chipCount = 0;
  for (const seen of seenChip) chipCount += seen.size;
  return {
    root,
    box: { x: 0, y: 0, w: width, h: height },
    lonely: rest.length,
    note: termNote(on, rowNodes.length, chipCount, offAxis),
  };
}

/**
 * 年表の凡例。**何を行にして、何を従にしたか**と、**出せなかった数**を書く。
 *
 * 書かないと「関係が減った」と読まれる —— 行どうしでない関係は線ではなく
 * チップになっているだけで、消してはいない。
 */
function termNote(axis, rows, chips, offAxis) {
  const head = axis === "when"
    ? `作中の時刻が書かれた語 ${rows} 件を、起きた順に並べています。`
    : `この文書に出てくる語 ${rows} 件を、読む順に並べています。`;
  const sub = chips
    ? `軸に載らない相手 ${chips} 件は、その行のうしろに小さく並べています（押すと開けます）。`
    : "";
  const off = offAxis
    ? `どちらの端も軸に載っていない関係が ${offAxis} 本あります（この図には出ません）。`
    : "";
  return head + sub + off;
}
