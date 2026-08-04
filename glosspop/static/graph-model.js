// 相関図の「形」だけを決める部分。**描き方を知らない。**
//
// 段の図 (`graph.js`) と、交差しない図 (`fabric.js`) の両方が同じ規則で読む
// ため、ここに置いてある。**片方だけに写しを作らないこと** —— 上下の段や
// 「関係の無い語」の扱いが 2 つの見せ方でずれると、同じ辞書なのに違うことを
// 言う図になる。

/**
 * `rank` から層を決める。`上` は「相手が自分より上」なので、辺の向きに関係なく
 * 上下の制約だけを見る。
 *
 * **`対等` は「同じ段」という制約**なので、先にまとめてから上下を解く。
 * これをやらないと、A と B が対等でも B だけが誰かの下に引っ張られて段が割れ、
 * 「上下は段で表す」という説明と食い違う（実際にそうなった）。
 * 閉路や矛盾した指定があっても回数で打ち切るので止まらなくはならない。
 */
export function levelsOf(nodes, edges) {
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

/**
 * 関係が 1 本も書かれていない語を段から外す。
 *
 * 段に混ぜると、**繋がっている語どうしを横へ押し広げるだけ**になり、そのぶん
 * 線が図の端から端まで飛ぶ。18 語のうち 6 語が孤立していた実例では、いちばん
 * 多く繋がっている語が最上段の右端に追いやられて図が読めなくなっていた。
 * 消しはしない（その文書に出てくる語ではある）ので、下に帯で並べる。
 */
export function splitLonely(nodes, edges) {
  const linked = new Set();
  for (const e of edges) {
    linked.add(e.from);
    linked.add(e.to);
  }
  return {
    linked: nodes.filter((n) => linked.has(n.ref)),
    lonely: nodes.filter((n) => !linked.has(n.ref)),
  };
}

/**
 * 繋がっているものを隣どうしにした初期の並び（ref → 通し番号）。
 *
 * **最初の段には「前の段」が無い。** 平均位置へ寄せる緩和だけでは、最初の段は
 * 入力順のまま残り、いちばん多く繋がっている語がたまたま端に居るとそこから
 * 全部の線が伸びる（実例がまさにそれだった）。多く繋がっているものから
 * 幅優先でたどって、componentごとにまとめた並びを緩和の出発点にする。
 */
export function seedOrder(nodes, edges) {
  const adj = new Map(nodes.map((n) => [n.ref, []]));
  for (const e of edges) {
    adj.get(e.from)?.push(e.to);
    adj.get(e.to)?.push(e.from);
  }
  const degree = (ref) => (adj.get(ref) || []).length;
  // 同点は入力順（サーバが返した順）で決める。乱数も時刻も混ぜない
  const index = new Map(nodes.map((n, i) => [n.ref, i]));
  const by = (a, b) => degree(b) - degree(a) || index.get(a) - index.get(b);
  const rank = new Map();
  const seen = new Set();
  for (const root of [...adj.keys()].sort(by)) {
    if (seen.has(root)) continue;
    seen.add(root);
    const queue = [root];
    while (queue.length) {
      const ref = queue.shift();
      rank.set(ref, rank.size);
      for (const next of [...(adj.get(ref) || [])].sort(by)) {
        if (seen.has(next)) continue;
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return rank;
}

