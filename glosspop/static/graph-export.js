// 相関図を画像として保存する。**6 つの見せ方すべてで同じ道を通る**
// （どれも `{ root, box }` を返すので、ここは「SVG を 1 枚もらう」だけで済む）。
//
// **画面の SVG をそのまま出しても崩れる。** 見た目は `style.css` のクラスと
// CSS 変数（`var(--accent)` など）で決まっていて、**外に出た SVG からはどちらも
// 引けない** —— 素の黒い線と黒い文字になる。だから**計算済みの値を要素へ焼き込む**。
//
// 焼き込むほうを選んだのは、CSS を丸ごと持ち出す形（`<style>` に貼る）だと
// **変数の解決とセレクタの取捨をこちらで再実装する**ことになるため。
// `getComputedStyle()` はブラウザが解決した結果なので、テーマ（ライト / ダーク）も
// 文字の大きさもそのまま付いてくる。
import { el } from "./base.js";

//: 焼き込む見た目。**位置と形は属性に入っている**ので、ここには持たない
//: （`x` `y` `points` `d` は clone がそのまま持っている）。
//: `display` を含めるのが要 —— **畳んだラベル**（`.tucked`）は画面で消えている
//: ので、外した状態のまま出す（画面と違う図を保存しない）
const PAINT = [
  "fill", "fill-opacity", "fill-rule",
  "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray",
  "stroke-linecap", "stroke-linejoin",
  "opacity", "display", "visibility",
  "font-family", "font-size", "font-weight", "font-style",
  "text-anchor", "dominant-baseline", "letter-spacing",
  "paint-order", "vector-effect", "marker-start", "marker-end",
];

/** 画面と同じ見た目を属性として焼き込む（元と写しを同時に歩く）。 */
function bakeStyles(source, clone) {
  const from = [source, ...source.querySelectorAll("*")];
  const to = [clone, ...clone.querySelectorAll("*")];
  for (let i = 0; i < from.length; i++) {
    const computed = getComputedStyle(from[i]);
    const bits = [];
    for (const name of PAINT) {
      const value = computed.getPropertyValue(name);
      if (value && value !== "auto" && value !== "normal") bits.push(`${name}:${value}`);
    }
    to[i].setAttribute("style", bits.join(";"));
    // **描かれない部品は落とす。** 当たり判定の透明な線と帯は、保存した図では
    // 何もしないのに要素数だけ増やす（PNG に変換するときの重さに効く）
    if (computed.display === "none") to[i].remove();
  }
}

/**
 * 外部の画像（地図の絵）を data URI に畳む。**畳まないと 1 枚で完結しない。**
 *
 * 保存した SVG は他のアプリで開かれるので、`/api/map?...` のままでは
 * **こちらのサーバが動いていないと絵が出ない**（渡した相手には絶対に出ない）。
 */
async function inlineImages(clone) {
  const images = [...clone.querySelectorAll("image")];
  await Promise.all(images.map(async (node) => {
    const href = node.getAttribute("href") || node.getAttribute("xlink:href") || "";
    if (!href || href.startsWith("data:")) return;
    try {
      const res = await fetch(href);
      const blob = await res.blob();
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      node.setAttribute("href", data);
      node.removeAttribute("xlink:href");
    } catch {
      // **読めなければ絵だけ落とす。** 図そのものは出す（黙って全部やめない）
      node.remove();
    }
  }));
}

/**
 * 保存できる 1 枚の SVG を組み立てる。``box`` は**図の全体**（いまの拡大率ではない）。
 *
 * 画面に出ているところだけを保存する形にしないこと —— 「図を保存」で切れた図が
 * 出てくるほうが驚く（拡大は見るための操作で、図の範囲ではない）。
 */
async function buildSvg(source, box, background) {
  const clone = source.cloneNode(true);
  bakeStyles(source, clone);
  await inlineImages(clone);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  clone.setAttribute("viewBox", `${box.x} ${box.y} ${box.w} ${box.h}`);
  // **寸法を書く。** 画面では入れ物が決めていたが、外では誰も決めてくれない
  // （地図の SVG に寸法が無いと歪む、と弾いているのと同じ話）
  clone.setAttribute("width", Math.round(box.w));
  clone.setAttribute("height", Math.round(box.h));
  clone.removeAttribute("class");
  // **背景を敷く。** 透明のままだと、ダークテーマで書き出した白い文字が
  // 白い紙の上で読めなくなる（画面で見えているとおりに保存する）
  if (background) {
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", box.x);
    rect.setAttribute("y", box.y);
    rect.setAttribute("width", box.w);
    rect.setAttribute("height", box.h);
    rect.setAttribute("fill", background);
    clone.prepend(rect);
  }
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
}

//: PNG にするときの倍率。**等倍だと図の文字が潰れる**（画面の SVG は
//: 拡大して読む前提で作ってある）。上げすぎるとブラウザが描けなくなるので 2 倍
const PNG_SCALE = 2;

async function toPng(svgText, box) {
  const url = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml" }));
  try {
    const image = await new Promise((resolve, reject) => {
      const node = new Image();
      node.onload = () => resolve(node);
      node.onerror = () => reject(new Error("図を画像にできませんでした"));
      node.src = url;
    });
    const canvas = el("canvas");
    canvas.width = Math.round(box.w * PNG_SCALE);
    canvas.height = Math.round(box.h * PNG_SCALE);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("図を画像にできませんでした"))),
        "image/png"
      );
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function save(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  // すぐ捨てるとダウンロードが始まらない環境があるので、1 拍おく
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/**
 * いまの図を保存する。``kind`` は ``"png"`` か ``"svg"``。
 *
 * **PNG は貼るため、SVG は拡大と編集のため**（どちらか一方にしない）。PNG に
 * できなかったときは黙って諦めず、呼ぶ側が理由を出せるように例外を投げる。
 */
export async function saveGraph(svgRoot, box, { kind = "png", name = "相関図" } = {}) {
  const blob = await figureBytes(svgRoot, box, { kind });
  save(blob, `${name}.${kind === "svg" ? "svg" : "png"}`);
}

/**
 * 図を**保存せずにバイト列で返す**（公開する経路が使う）。
 *
 * 手元に落とすのと**同じ道を通す** —— 見た目の焼き込みも地図の絵の畳み込みも
 * ここ 1 か所にあるので、**公開した絵と手元に落とした絵が食い違わない**。
 * 別に組み立てる形にすると、片方だけ素の黒い線になる（そして気付けない）。
 */
export async function figureBytes(svgRoot, box, { kind = "png" } = {}) {
  if (!svgRoot || !box) throw new Error("保存できる図がありません");
  const background = getComputedStyle(svgRoot.parentElement || document.body).backgroundColor;
  const opaque = background && background !== "rgba(0, 0, 0, 0)" ? background : "";
  const svgText = await buildSvg(svgRoot, box, opaque);
  if (kind === "svg") return new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
  return toPng(svgText, box);
}
