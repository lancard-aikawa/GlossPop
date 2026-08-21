// カード（メタ画像）を 1 枚の SVG として組む。
//
// **`{ root, box }` を返す**ので、書き出しは `graph-export.js` の道がそのまま
// 使える（図の見せ方 6 つと同じ約束。ここに PNG 化を書かない）。
//
// **色は CSS 変数から引かない。** 図はその場で見るものなのでテーマに従ってよいが、
// カードは**外へ出て、見る人の設定とは無関係に表示される**。変数にすると、
// 書き出した人がライトかダークかで**配られる絵が変わる**。ここは焼き切る。
//
// **字幅は estTextWidth で見積もらず、実際に測る**（`getComputedTextLength()`）。
// 見積もりだと、長い用語名の辞書で**チップが枠から出る**か、手前で切れて
// 入るはずのものが落ちる。測るには**文書に付いている**必要があるので、
// `host` は画面にある要素であること（隠してあってよい）。
import { svgEl } from "./base.js";

//: X の大きいカード（`summary_large_image`）の比。1.91:1
export const CARD_W = 1200;
export const CARD_H = 630;

const PAD = 60;
const INK = {
  bg: "#17181a",
  fg: "#e7e7e4",
  dim: "#a6a8a5",
  faint: "#7c7f7c",
  accent: "#5eead4",
  chip: "#22262a",
  chipEdge: "#33383d",
};
const FONT = '"Yu Gothic UI","Hiragino Sans","Noto Sans JP",system-ui,sans-serif';

const TITLE_SIZE = 68;
const TITLE_LINE = 84;
const TITLE_MAX_LINES = 2;
const NOTE_SIZE = 27;
const CHIP_SIZE = 30;
const CHIP_PAD_X = 16;
const CHIP_H = 46;
const CHIP_GAP = 13;
const FOOT_SIZE = 26;

//: 行頭に置かない字（最小限の禁則）。**題は機械が作るので凝らない**
const NOT_AT_HEAD = "、。，．）」』】〉》・ー…";

function text(value, attrs) {
  const node = svgEl("text", {
    "font-family": FONT,
    "dominant-baseline": "text-before-edge",
    ...attrs,
  });
  node.textContent = value;
  return node;
}

/** 実測用のものさし。**使い終わったら片付ける。** */
function ruler(root) {
  const probe = text("", { x: -9999, y: -9999, "font-family": FONT });
  root.append(probe);
  return {
    width(value, size, weight = "normal") {
      probe.setAttribute("font-size", size);
      probe.setAttribute("font-weight", weight);
      probe.textContent = value;
      return probe.getComputedTextLength();
    },
    done() {
      probe.remove();
    },
  };
}

/** 幅で折り返す。日本語には語境界が無いので 1 字ずつ積む。 */
function wrap(value, { width, size, weight, measure, maxLines }) {
  const lines = [];
  let line = "";
  for (const ch of value) {
    const next = line + ch;
    if (line && measure(next, size, weight) > width && !NOT_AT_HEAD.includes(ch)) {
      lines.push(line);
      line = ch;
      if (lines.length === maxLines) return { lines, rest: true };
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return { lines, rest: false };
}

/** 収まるところで切る。**切れないときは何も足さない**（「…」だけの行を作らない）。 */
function clip(value, { width, size, measure }) {
  if (measure(value, size) <= width) return value;
  let out = "";
  for (const ch of value) {
    if (measure(`${out}${ch}…`, size) > width) break;
    out += ch;
  }
  return out ? `${out}…` : "";
}

function chip(label, x, y, w) {
  const group = svgEl("g");
  group.append(svgEl("rect", {
    x, y, width: w, height: CHIP_H, rx: CHIP_H / 2,
    fill: INK.chip, stroke: INK.chipEdge, "stroke-width": 1,
  }));
  group.append(text(label, {
    x: x + w / 2, y: y + (CHIP_H - CHIP_SIZE) / 2 - 1,
    "font-size": CHIP_SIZE, fill: INK.fg, "text-anchor": "middle",
  }));
  return group;
}

/**
 * カードを描く。``host`` は**画面にある**入れ物（隠れていてよい）。
 *
 * 返すのは `{ root, box, shown, dropped }`。**入り切らなかった語は数で返す**
 * ので、呼ぶ側が「他 N 語」として画面にも出せる（黙って欠けた絵を出さない、の
 * 約束。`hidden` / `outside` / `tucked` と同じ扱い）。
 */
export function drawCard(card, { host } = {}) {
  if (!host) throw new Error("カードを測るには画面にある入れ物が要ります");
  const root = svgEl("svg", {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: `0 0 ${CARD_W} ${CARD_H}`,
    width: CARD_W, height: CARD_H, class: "gloss-card",
  });
  host.append(root);
  const measure = ruler(root);
  const inner = CARD_W - PAD * 2;

  root.append(svgEl("rect", { x: 0, y: 0, width: CARD_W, height: CARD_H, fill: INK.bg }));

  let y = PAD;
  const title = wrap(card.title || card.name || "", {
    width: inner, size: TITLE_SIZE, weight: "800",
    measure: measure.width, maxLines: TITLE_MAX_LINES,
  });
  for (const line of title.lines) {
    root.append(text(line, {
      x: PAD, y, "font-size": TITLE_SIZE, "font-weight": "800", fill: INK.fg,
    }));
    y += TITLE_LINE;
  }

  if (card.note) {
    y += 4;
    root.append(text(clip(card.note, { width: inner, size: NOTE_SIZE, measure: measure.width }), {
      x: PAD, y, "font-size": NOTE_SIZE, fill: INK.dim,
    }));
    y += NOTE_SIZE + 22;
  } else {
    y += 18;
  }

  root.append(svgEl("rect", { x: PAD, y, width: inner, height: 3, fill: INK.accent }));
  y += 3 + 26;

  // --- チップを敷き詰める。**入る数は測って決める**（定数を持たない）
  const footTop = CARD_H - PAD - FOOT_SIZE - 8;
  const widths = (card.terms || []).map(
    (term) => measure.width(term, CHIP_SIZE) + CHIP_PAD_X * 2
  );
  const rows = [];
  let row = [];
  let used = 0;
  for (let i = 0; i < widths.length; i++) {
    const w = widths[i];
    if (row.length && used + CHIP_GAP + w > inner) {
      rows.push(row);
      row = [];
      used = 0;
      if (y + (rows.length + 1) * (CHIP_H + CHIP_GAP) > footTop) break;
    }
    row.push({ term: card.terms[i], w });
    used += (row.length > 1 ? CHIP_GAP : 0) + w;
  }
  if (row.length && y + (rows.length + 1) * (CHIP_H + CHIP_GAP) <= footTop) rows.push(row);

  let shown = rows.reduce((n, r) => n + r.length, 0);
  let dropped = (card.terms || []).length - shown;
  if (dropped > 0 && rows.length) {
    // 「他 N 語」の席を最後の行に作る。**入らなければ 1 つ譲る**
    const last = rows[rows.length - 1];
    const label = () => `他 ${(card.terms || []).length - shown} 語`;
    let mark = measure.width(label(), CHIP_SIZE) + CHIP_PAD_X * 2;
    let width = last.reduce((n, c) => n + c.w, 0) + CHIP_GAP * last.length;
    while (last.length && width + mark > inner) {
      width -= last.pop().w + CHIP_GAP;
      shown -= 1;
      mark = measure.width(label(), CHIP_SIZE) + CHIP_PAD_X * 2;
    }
    dropped = (card.terms || []).length - shown;
    last.push({ term: label(), w: mark, more: true });
  }

  let chipY = y;
  for (const line of rows) {
    let x = PAD;
    for (const item of line) {
      const node = chip(item.term, x, chipY, item.w);
      if (item.more) {
        const box = node.querySelector("rect");
        box.setAttribute("fill", "none");
        box.setAttribute("stroke-dasharray", "6 5");
        node.querySelector("text").setAttribute("fill", INK.faint);
      }
      root.append(node);
      x += item.w + CHIP_GAP;
    }
    chipY += CHIP_H + CHIP_GAP;
  }

  // --- 足もと。**左下は空けておく**
  //
  // **実測**: X は貼られたカードの**左下に見出しの札を重ねる**（画像の上に
  // 白い角丸で「同じ日に、2 つの事件」と出る）。そこに何か置くと隠れるので、
  // 数も名前も右へ寄せる。左半分は意図的に空白のまま
  const foot = [
    `${card.total} 語 ・ ${card.links} 本の関係`,
    card.name || "GlossPop",
  ].join("　");
  root.append(text(foot, {
    x: CARD_W - PAD, y: CARD_H - PAD - FOOT_SIZE,
    "font-size": FOOT_SIZE, fill: INK.faint, "text-anchor": "end",
  }));

  measure.done();
  return { root, box: { x: 0, y: 0, w: CARD_W, h: CARD_H }, shown, dropped };
}
