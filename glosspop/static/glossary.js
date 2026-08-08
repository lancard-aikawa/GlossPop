// 辞書一覧: カテゴリマスターの順にグルーピングして表示。
import { api, el, esc, paintEntryCount, setStatus } from "./base.js";
import { openEntryEditor } from "./editor.js";
import { installSelectionAdd } from "./select-add.js";
import { invalidatePopupCache } from "./popup.js";

//: 画面の中身。**ここが唯一の出どころ**（HTML 側に写しを置かない）
const TEMPLATE = `
<h1>用語辞書</h1>
<p class="lede" id="lede"></p>

<div class="toolbar">
  <input type="search" id="q" placeholder="用語・別名・本文を検索" autocomplete="off"
         title="用語・別名・本文を検索" aria-label="用語・別名・本文を検索">
  <select id="catFilter" class="auto-width" title="カテゴリで絞り込む" aria-label="カテゴリで絞り込む">
    <option value="">すべてのカテゴリ</option>
  </select>
  <select id="tagFilter" class="auto-width" title="タグで絞り込む" aria-label="タグで絞り込む">
    <option value="">すべてのタグ</option>
  </select>
  <!-- 束ね方。**カテゴリ順の置き換えではなく、もう 1 つの引き方**（紙の辞書で
       いちばん普通の「あいうえお順に通して見る」道が無かった）。覚える -->
  <select id="groupBy" class="auto-width" title="束ね方" aria-label="束ね方">
    <option value="category">カテゴリ順</option>
    <option value="reading">五十音順</option>
  </select>
  <span class="spacer"></span>
  <!-- 索引（語がどこに何回出てくるか）。**辞書の側から本文を見る唯一の入口**
       で、いちばん見たいのは「登録したのに 1 度も出てこない語」のほう -->
  <a class="btn" href="/occurrences" title="登録した語が本文のどこに出てくるかを並べる">📇 索引</a>
  <!-- 冊子。zip は**データの持ち運び**で、**人に渡して読ませる形**が無かった -->
  <button type="button" id="booklet" title="辞書を 1 枚にまとめて書き出す（読む用）">📖 冊子</button>
  <button type="button" id="manageCats">カテゴリ管理</button>
  <button type="button" class="primary" id="add">＋ 新規登録</button>
</div>

<div id="list"></div>

<!-- 冊子の書き出し。**形式と索引の有無だけ**を聞く（それ以上を聞くと、
     押す前に何が出るのか分からなくなる） -->
<dialog class="sheet" id="bookletDialog">
  <div class="edge-editor">
    <header>
      <h2>冊子として書き出す</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="bkClose" aria-label="閉じる">✕</button>
    </header>
    <p class="hint">
      辞書ぜんぶを<strong>五十音順の 1 枚</strong>にします（zip とは別物 ——
      あちらはデータの持ち運び、これは読んで渡すため）。
      Markdown は GitHub やエディタで、HTML はそのまま印刷して読めます。
    </p>
    <div class="cat-row">
      <select data-ref="bkFormat" class="auto-width" aria-label="形式">
        <option value="md">Markdown（GitHub・エディタ）</option>
        <option value="html">HTML（印刷・配る）</option>
      </select>
      <label class="check" data-ref="bkIndexBox">
        <input type="checkbox" data-ref="bkIndex">
        <span>巻末索引も入れる</span>
      </label>
    </div>
    <p class="hint">
      索引を入れると、<strong>開いているフォルダの本文を読みます</strong>
      （語がどこに出てくるかを巻末に並べます）。そのぶん時間がかかります。
    </p>
    <footer>
      <span class="status" data-ref="bkStatus"></span>
      <div class="spacer"></div>
      <button type="button" class="primary" data-ref="bkGo">⬇ 書き出す</button>
    </footer>
  </div>
</dialog>

<dialog class="sheet" id="catDialog">
  <div class="cat-manager">
    <header>
      <h2>カテゴリ管理</h2>
      <div class="spacer"></div>
      <button type="button" class="ghost" data-ref="catClose" aria-label="閉じる">✕</button>
    </header>
    <div class="body" data-ref="catBody"></div>
    <footer>
      <select data-ref="catScope" title="どちらの辞書に作るか" aria-label="どちらの辞書に作るか" hidden>
        <option value="global">全体の辞書</option>
        <option value="local">📁 このフォルダの辞書</option>
      </select>
      <input type="text" data-ref="catNew" class="auto-width" placeholder="新しいカテゴリ名"
             title="新しいカテゴリ名" aria-label="新しいカテゴリ名" autocomplete="off">
      <button type="button" data-ref="catAdd">＋ 追加</button>
      <span class="status" data-ref="catStatus"></span>
      <span class="spacer"></span>
      <span class="hint">用語 0 件のカテゴリも登録できます。並びは ↑ ↓ で変えられます。削除できるのは空のカテゴリだけです。</span>
    </footer>
  </div>
</dialog>
`;

//: 描く先。`mount()` で埋める（`location` は読まない —— 重ねたときに
//: 「覆いが出しているもの」と食い違う）
let host = null;
let list, qInput, catFilter, tagFilter, catDialog, groupBy;

//: 束ね方を覚える鍵。**読めない値は「カテゴリ順」に落ちる**（既定を壊さない）
const GROUP_KEY = "glosspop.glossaryGroup";
const $ = (id) => host.querySelector(`#${id}`);

let tree = [];
let timer = null;
//: 直前に描いた一覧。束ね方を変えるだけなら、これを束ね直す
let lastEntries = null;
//: 最初の読み込み。**開くのが速すぎたダイアログがこれを待って描き直す**
let ready = null;

/** 選択したまま指を離したか。カードの上でのドラッグ選択と、リンクの遷移を分ける。 */
function hasSelection() {
  const sel = window.getSelection();
  return Boolean(sel && !sel.isCollapsed && sel.toString().trim());
}

/**
 * 1 枚のカード。**カード全体を `<a>` にしない。**
 *
 * `<a>` の中ではドラッグが「リンクを掴む」になり、要約の語を選べない
 * （`draggable="false"` を付けても Chrome は選択させてくれない。実際に試した）。
 * 一覧にだけ選択 → 登録の口が無かった理由がこれ。
 *
 * 代わりに、**見出しだけを本物のリンク**にしてカード全体はクリックで飛ばす。
 * こうすると見出しの中クリック・Ctrl クリック（別タブ）はそのまま効き、
 * 要約の上ではふつうに文字を選べる。
 */
function card(e, { showPath = false } = {}) {
  const node = el("div", { class: e.image_url ? "card has-thumb" : "card" }, [
    // **ペルソナの顔は一覧には出さない。** 顔は辞書に 1 枚なので、同じ辞書の
    // カードが並ぶと同じ絵が何十個も繰り返されるだけで、何も区別できない
    // （出す価値があるのは、複数の辞書が並ぶ吹き出しと、1 件を見る用語ページ）。
    // **用語ごとの画像は逆で、語ごとに違うので見分けに効く** —— 出す。
    // **`loading="lazy"` は外さないこと**（一覧は数千枚になりうる）
    e.image_url ? el("img", {
      class: "card-thumb", src: e.image_url, alt: "", loading: "lazy",
    }) : null,
    el("a", {
      class: "t", href: e.url,
      html: esc(e.term) + (e.reading ? `<span class="r">${esc(e.reading)}</span>` : ""),
    }),
    el("div", { class: "s", text: e.summary || (e.aliases?.length ? `別名: ${e.aliases.join(" / ")}` : "（要約なし）") }),
    // **読み順で束ねるときは、どのカテゴリの語なのかをカードに出す。**
    // 見出しがカテゴリでなくなるぶん、ここに書かないと「ソース（料理）」と
    // 「ソース（プログラミング）」が並んでも見分けられない
    showPath ? el("div", { class: "card-path", text: e.path_label }) : null,
  ]);
  node.addEventListener("click", (ev) => {
    if (ev.target.closest("a")) return;      // 見出しのリンクはブラウザに任せる
    if (hasSelection()) return;              // 選んでいる最中は飛ばさない
    location.href = e.url;
  });
  return node;
}

/**
 * グルーピングの鍵。**カテゴリ名だけでは割れない。**
 *
 * 同じ名前のカテゴリが全体とフォルダの両方にありうるので、名前で束ねると
 * 2 つの辞書の用語が 1 つの見出しに混ざり、マスターの順で並べるときに
 * 同じ見出しが 2 回出る。区切りは `<>`（カテゴリ名でも slug でも弾かれる）。
 */
const groupKey = (scope, category) => `${scope}<>${category}`;

// --------------------------------------------------------------------------- //
// 五十音で通して引く
//
// カテゴリで束ねるのは「どういう語か」で引く道で、**辞書を通してあいうえお順に
// 見る道**が無かった（紙の辞書でいちばん普通の引き方）。**カテゴリ順と置き換えず、
// 選べるようにする**（見せ方を足すもので置き換えではない、と同じ判断）。
// --------------------------------------------------------------------------- //

//: 行と、そこに入る先頭の字。**この順に出す。** 濁点・半濁点・小さい字も
//: 同じ行に入れる（「が」は か行、「ゃ」は や行）—— 別の行に散ると探せない
const KANA_ROWS = [
  ["あ", "あいうえおぁぃぅぇぉゔ"],
  ["か", "かきくけこがぎぐげごゕゖ"],
  ["さ", "さしすせそざじずぜぞ"],
  ["た", "たちつてとだぢづでどっ"],
  ["な", "なにぬねの"],
  ["は", "はひふへほばびぶべぼぱぴぷぺぽ"],
  ["ま", "まみむめも"],
  ["や", "やゆよゃゅょ"],
  ["ら", "らりるれろ"],
  ["わ", "わをんゎ"],
];
const ROW_LATIN = "英字";
const ROW_DIGIT = "数字";
//: かなで置けない語を入れる束。**黙って「あ」行に混ぜない** ——
//: 漢字の見出しは、読みを書かないかぎりどの行にも置けない
const ROW_NONE = "読みなし";
const ROW_ORDER = [...KANA_ROWS.map(([row]) => row), ROW_LATIN, ROW_DIGIT, ROW_NONE];

/**
 * 先頭の 1 字から行を決める。決まらなければ空。
 *
 * **カタカナはひらがなに畳む**（同じ音は同じ行）。長音符「ー」は前の音が
 * 分からないと置けないので、決まらない側に倒す。
 */
function rowOfText(text) {
  const first = [...String(text || "").trim()][0];
  if (!first) return "";
  const code = first.codePointAt(0);
  // カタカナ → ひらがな（ヴまで）。ここを外すと「ジョバンニ」が読みなしに落ちる
  const kana = code >= 0x30a1 && code <= 0x30f6 ? String.fromCodePoint(code - 0x60) : first;
  for (const [row, chars] of KANA_ROWS) if (chars.includes(kana)) return row;
  if (/[A-Za-z]/.test(first)) return ROW_LATIN;
  if (/[0-9０-９]/.test(first)) return ROW_DIGIT;
  return "";
}

/** その語をどの行に置くか。**読みが正、無ければ見出しそのもの。** */
function rowOf(e) {
  return rowOfText(e.reading) || rowOfText(e.term) || ROW_NONE;
}

/** 読み（無ければ見出し）で並べる。同じなら ref で決め切る（並びを揺らさない）。 */
function byReading(a, b) {
  return (a.reading || a.term).localeCompare(b.reading || b.term, "ja")
    || a.ref.localeCompare(b.ref);
}

/**
 * 行の見出しへ飛ぶ帯。**中身のある行だけ出す**（押しても何も起きない字を並べない）。
 *
 * 飛び先は id ではなく `data-row` で引く —— 覆いで開くと同じ document に
 * 複数の画面が居るので、id を撒くと衝突する。
 */
function jumpBar(rows) {
  return el("div", { class: "kana-bar", "data-ref": "kanaBar" }, rows.map(([row, items]) =>
    el("button", {
      type: "button",
      class: "chip",
      "data-jump": row,
      text: `${row} ${items.length}`,
      title: row === ROW_NONE ? "読みが書かれていない語（かなで置けない）" : `${row} 行へ`,
      onclick: () => {
        const target = list.querySelector(`[data-row="${CSS.escape(row)}"]`);
        target?.scrollIntoView({ block: "start", behavior: "smooth" });
      },
    })));
}

/**
 * 「読みなし」の束に付ける、読みを埋める道。**入力欄は 1 つ、埋め方が 2 つ。**
 *
 * 手で書いても AI に埋めさせても**同じ欄**に入り、**同じ 1 回の保存**で書き込む
 * （`POST /api/readings`）。別々の口にすると、AI が埋めたぶんだけ先に保存されて
 * 「手で直したのに戻った」が起きる。
 *
 * **AI の答えをそのまま保存しない。** かなでないものはサーバが落とすし、
 * 落ちたものは理由つきで出す —— 読みは間違っていると**辞書の並びが狂う**ので、
 * 書かれていないほうがましな場面がある（人が見てから保存する）。
 */
function readingForm(items) {
  const status = el("span", { class: "status", "data-ref": "readStatus" });
  const inputs = new Map();

  const save = async () => {
    // **空の欄は送らない。** 空文字は「読みを消す」の意味なので、触っていない
    // 欄まで送ると、AI が埋めなかった語を消しに行くことになる
    const readings = [...inputs]
      .map(([ref, input]) => ({ ref, reading: input.value.trim() }))
      .filter((r) => r.reading);
    if (!readings.length) {
      setStatus(status, "読みが入っていません", "error");
      return;
    }
    setStatus(status, "保存中", "busy");
    try {
      const res = await api("/api/readings", { method: "POST", body: { readings } });
      invalidatePopupCache();
      setStatus(status, `${res.applied} 語に読みを書きました`);
      await reload();          // 書いたぶんはその行へ移る（消えるところまで見せる）
    } catch (err) {
      setStatus(status, err.message, "error");
    }
  };

  const draft = async (button) => {
    button.disabled = true;
    setStatus(status, "AI が読みを考えています", "busy");
    try {
      const res = await api("/api/ai/readings", {
        method: "POST",
        body: { refs: [...inputs.keys()] },
      });
      for (const hit of res.readings) {
        const input = inputs.get(hit.ref);
        // **手で書いたぶんを上書きしない**（先に書いた人の指定が勝つ）
        if (input && !input.value.trim()) input.value = hit.reading;
      }
      const note = res.dropped.length ? `／埋まらなかった ${res.dropped.length} 語: `
        + res.dropped.slice(0, 3).map((d) => `${d.term}（${d.why}）`).join("、") : "";
      setStatus(status, `${res.readings.length} 語を埋めました${note}`);
    } catch (err) {
      setStatus(status, err.message, "error");
    }
    button.disabled = false;
  };

  const aiButton = el("button", {
    type: "button",
    "data-ref": "readDraft",
    text: "✨ 読みを下書き",
    title: "AI に読みを考えさせて、下の欄に入れる（保存はしません）",
  });
  aiButton.addEventListener("click", () => draft(aiButton));

  const rows = items.map((e) => {
    const input = el("input", {
      type: "text",
      class: "narrow",
      "data-ref": "readInput",
      "data-ref-of": e.ref,
      placeholder: "よみ",
      "aria-label": `${e.term} の読み`,
      // Enter でそのまま保存（1 語だけ直したいときに、ボタンまで行かせない）
      onkeydown: (ev) => {
        if (ev.key === "Enter") save();
      },
    });
    inputs.set(e.ref, input);
    return el("li", { class: "rel-row" }, [
      el("a", { class: "chip", href: e.url, text: e.term, title: e.path_label }),
      el("span", { class: "hint", text: e.path_label }),
      input,
    ]);
  });

  return el("div", { class: "reading-form" }, [
    el("p", { class: "hint", text: "読みを書くと、その行に並びます。手で書いても、AI に下書きさせてもかまいません。" }),
    el("ul", { class: "rel-list" }, rows),
    el("div", { class: "cat-row" }, [
      aiButton,
      el("button", { type: "button", class: "primary", "data-ref": "readSave", text: "読みを保存", onclick: save }),
      status,
    ]),
  ]);
}

/**
 * 冊子を書き出す。**保存はブラウザに任せる**（サーバが `Content-Disposition` を
 * 付けて返すので、ここは窓を開けるだけ）。
 *
 * 索引を入れると本文を読むので**待たされる** —— 押したあと何も起きないように
 * 見えないよう、状況を出してから開く。
 */
function installBooklet() {
  const dialog = $("bookletDialog");
  const ref = (name) => dialog.querySelector(`[data-ref=${name}]`);
  // **listener は開く前に付ける**（開いた瞬間の操作を落とさない）
  ref("bkClose").addEventListener("click", () => dialog.close());
  ref("bkGo").addEventListener("click", () => {
    const query = new URLSearchParams({
      fmt: ref("bkFormat").value,
      index: ref("bkIndex").checked ? "true" : "false",
    });
    setStatus(
      ref("bkStatus"),
      ref("bkIndex").checked ? "本文を読んでいます（少し待ちます）" : "書き出しています",
      "busy",
    );
    // 別窓ではなく同じ窓の遷移で落とす（ダウンロードなのでページは変わらない）
    location.href = `/api/booklet?${query}`;
    setTimeout(() => setStatus(ref("bkStatus"), "書き出しました"), 1200);
  });
  $("booklet").addEventListener("click", () => {
    setStatus(ref("bkStatus"), "");
    dialog.showModal();
  });
}

/** 五十音で束ねて描く。**カテゴリはカードに出す**（見出しがカテゴリでなくなるので）。 */
function paintByReading(entries) {
  const buckets = new Map(ROW_ORDER.map((row) => [row, []]));
  for (const e of entries) buckets.get(rowOf(e)).push(e);
  const rows = ROW_ORDER
    .map((row) => [row, buckets.get(row).sort(byReading)])
    .filter(([, items]) => items.length);

  if (!rows.length) {
    list.replaceChildren(el("p", { class: "empty", text: "該当する用語がありません" }));
    return;
  }
  list.replaceChildren(
    jumpBar(rows),
    ...rows.map(([row, items]) => el("section", { class: "cat-group", "data-row": row }, [
      el("h2", {}, [
        el("span", { text: row }),
        el("span", { class: "count", text: `${items.length} 語` }),
      ]),
      // **読みが無いことを責めない。** 埋める道をその場に置くだけ
      // （カードではなく一覧にするのは、続けて何語も書く場所だから）
      row === ROW_NONE
        ? readingForm(items)
        : el("div", { class: "cards" }, items.map((e) => card(e, { showPath: true }))),
    ])),
  );
}

function paint(entries) {
  // **束ね方を変えるだけならサーバへ行き直さない**（同じものを束ね直すだけ）
  lastEntries = entries;
  if (groupBy.value === "reading") {
    paintByReading(entries);
    return;
  }
  const filtering = Boolean(qInput.value.trim() || catFilter.value || tagFilter.value);
  const byCategory = new Map();
  for (const e of entries) {
    const gk = groupKey(e.scope, e.category);
    if (!byCategory.has(gk)) byCategory.set(gk, new Map());
    const subs = byCategory.get(gk);
    const key = e.subcategory || "";
    if (!subs.has(key)) subs.set(key, []);
    subs.get(key).push(e);
  }

  // マスターの順で並べ、マスターに無いカテゴリは後ろに付ける
  const order = tree.map((n) => groupKey(n.scope, n.category));
  for (const gk of byCategory.keys()) if (!order.includes(gk)) order.push(gk);

  const nodes = [];
  for (const gk of order) {
    const subs = byCategory.get(gk);
    const meta = tree.find((n) => groupKey(n.scope, n.category) === gk);
    const category = meta ? meta.category : gk.slice(gk.indexOf("<>") + 2);
    const heading = meta ? categoryLabel(meta) : category;
    if (!subs) {
      // 絞り込み中に空カテゴリを見せても邪魔なので、素の一覧のときだけ出す
      if (filtering) continue;
      nodes.push(el("section", { class: "cat-group empty-cat" }, [
        el("h2", {}, [
          el("span", { text: heading }),
          el("span", { class: "count", text: "0 語" }),
          meta?.description ? el("span", { class: "count", text: meta.description }) : null,
        ]),
      ]));
      continue;
    }
    const total = [...subs.values()].reduce((n, arr) => n + arr.length, 0);
    const group = el("section", { class: "cat-group" }, [
      el("h2", {}, [
        el("span", { text: heading }),
        el("span", { class: "count", text: `${total} 語` }),
      ]),
    ]);
    const subKeys = [...subs.keys()].sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b, "ja")));
    for (const sub of subKeys) {
      const items = subs.get(sub).sort((a, b) => (a.reading || a.term).localeCompare(b.reading || b.term, "ja"));
      const wrap = el("div", { class: "sub-group" });
      if (sub) wrap.append(el("h3", { text: sub }));
      wrap.append(el("div", { class: "cards" }, items.map(card)));
      group.append(wrap);
    }
    nodes.push(group);
  }

  if (!nodes.length) {
    list.replaceChildren(
      el("p", { class: "empty", text: filtering ? "該当する用語がありません" : "まだ用語が登録されていません" })
    );
    return;
  }
  list.replaceChildren(...nodes);
}

async function reload() {
  const params = new URLSearchParams();
  if (qInput.value.trim()) params.set("q", qInput.value.trim());
  const picked = categorySelection();
  if (picked.category) {
    params.set("category", picked.category);
    params.set("scope", picked.scope);
  }
  if (tagFilter.value) params.set("tag", tagFilter.value);
  const qs = params.toString();
  list.setAttribute("aria-busy", "true");
  try {
    const entries = await api(`/api/entries${qs ? "?" + qs : ""}`);
    paint(entries);
    const dup = countDuplicates(entries);
    $("lede").textContent =
      `${entries.length} 語 / ${tree.length} カテゴリ` +
      (dup ? ` — ${dup} 語がカテゴリ違いで重複しています` : "");
  } catch (err) {
    list.replaceChildren(el("p", { class: "status error", text: err.message }));
  } finally {
    list.removeAttribute("aria-busy");
  }
}

function countDuplicates(entries) {
  const seen = new Map();
  for (const e of entries) seen.set(e.term, (seen.get(e.term) || 0) + 1);
  return [...seen.values()].filter((n) => n > 1).length;
}

async function loadCategories() {
  try {
    tree = await api("/api/categories");
  } catch {
    tree = [];
  }
  const current = catFilter.value;
  // **値にスコープを持たせる。** 同じ名前のカテゴリが全体とフォルダの両方にあると、
  // 名前だけでは見分けが付かない。区切りは "/"（カテゴリ名では禁止）で 1 回だけ割る
  catFilter.replaceChildren(
    el("option", { value: "", text: "すべてのカテゴリ" }),
    ...tree.map((n) =>
      el("option", { value: `${n.scope}/${n.category}`, text: `${categoryLabel(n)} (${n.count})` })
    ),
  );
  catFilter.value = current;
}

/** 絞り込みの選択を {scope, category} に戻す。 */
function categorySelection() {
  if (!catFilter.value) return { scope: null, category: null };
  const cut = catFilter.value.indexOf("/");
  return {
    scope: catFilter.value.slice(0, cut),
    category: catFilter.value.slice(cut + 1),
  };
}

/**
 * タグの選択肢。**マスターが無い**ので、使われているものを数えてもらう。
 *
 * 1 つだけ選ぶ形にしてある。複数選べるようにすると「AND か OR か」を決めることに
 * なり、カテゴリの絞り込みとも見た目が揃わない。必要になってから広げる。
 */
async function loadTags() {
  let tags = [];
  try {
    tags = await api("/api/tags");
  } catch {
    tags = [];
  }
  const current = tagFilter.value;
  tagFilter.replaceChildren(
    el("option", { value: "", text: "すべてのタグ" }),
    ...tags.map((t) => el("option", { value: t.name, text: `#${t.name} (${t.count})` })),
  );
  // 選んでいたタグが最後の 1 語から外れると選択肢ごと消える。その場合は「すべて」に戻る
  tagFilter.value = current;
  tagFilter.disabled = !tags.length;
}

// ------------------------------------------------------------ カテゴリ管理

/** ローカルは 📁 を付ける。付けないと、どちらの辞書を触るのか分からない。 */
function categoryLabel(node) {
  return node.scope === "local" ? `📁 ${node.category}` : node.category;
}

/** そのスコープの中での位置。並べ替えは辞書を跨がない。 */
function siblings(scope) {
  return tree.filter((n) => n.scope === scope);
}

function categoryRow(node) {
  const nameNode = el("div", { class: "cat-row-name", text: categoryLabel(node) });
  const meta = el("div", { class: "cat-row-meta", text:
    `${node.count} 語` +
    (node.subcategories.filter((s) => s.name).length
      ? ` · ${node.subcategories.filter((s) => s.name).map((s) => s.name).join(" / ")}`
      : "") +
    (node.description ? ` · ${node.description}` : "") +
    (node.scope === "local" ? " · このフォルダの辞書" : "")
  });
  const peers = siblings(node.scope);
  const at = peers.findIndex((n) => n.category === node.category);
  const row = el("div", { class: "cat-row" }, [
    el("div", { class: "cat-row-main" }, [nameNode, meta]),
    el("button", {
      type: "button", class: "ghost", text: "↑", title: "1 つ上へ",
      "aria-label": `${node.category} を 1 つ上へ`,
      disabled: at <= 0,
      onclick: () => moveCategory(node, -1),
    }),
    el("button", {
      type: "button", class: "ghost", text: "↓", title: "1 つ下へ",
      "aria-label": `${node.category} を 1 つ下へ`,
      disabled: at < 0 || at >= peers.length - 1,
      onclick: () => moveCategory(node, 1),
    }),
    el("button", { type: "button", text: "名前を変更", onclick: () => startRename(row, node) }),
    el("button", {
      type: "button", class: "danger", text: "削除",
      disabled: node.count > 0,
      title: node.count > 0 ? "用語が入っているカテゴリは削除できません" : "",
      onclick: () => removeCategory(node),
    }),
  ]);
  return row;
}

/**
 * 1 つ上 / 下へ動かす。**送るのはそのスコープの全体の並び**（差分ではない）。
 *
 * 「これを 1 つ上へ」を送る形にすると、続けて押したときに後の書き込みが前の
 * ものを消す（関係の書き込みを 1 本ずつ PUT しない、と同じ理由）。
 */
async function moveCategory(node, delta) {
  const names = siblings(node.scope).map((n) => n.category);
  const at = names.indexOf(node.category);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= names.length) return;
  names.splice(to, 0, ...names.splice(at, 1));
  try {
    await api("/api/category-order", { method: "PUT", body: { names, scope: node.scope } });
    await refreshAll();
  } catch (err) {
    alert(`並べ替えできません: ${err.message}`);
  }
}

/** 行をその場で入力欄に差し替える (prompt を使わない)。 */
function startRename(row, node) {
  const input = el("input", { type: "text", value: node.category, "aria-label": "新しいカテゴリ名" });
  const status = el("span", { class: "status" });
  const save = el("button", {
    type: "button", class: "primary", text: "保存",
    onclick: async () => {
      const next = input.value.trim();
      if (!next || next === node.category) return row.replaceWith(categoryRow(node));
      save.disabled = true;
      setStatus(status, "変更中", "busy");
      try {
        // マスターは辞書ごとにあるので、どちらでも同じものを送る。
        // **サブカテゴリを送らないと空リストで上書きされる**（省略と「全部消す」
        // を区別するため、サーバ側の既定は null）
        const body = {
          name: next,
          subcategories: node.subcategories.map((s) => s.name).filter(Boolean),
        };
        await api(
          `/api/categories/${encodeURIComponent(node.category)}?scope=${node.scope}`,
          { method: "PUT", body }
        );
        await refreshAll();
      } catch (err) {
        setStatus(status, err.message, "error");
        save.disabled = false;
      }
    },
  });
  const editing = el("div", { class: "cat-row" }, [
    el("div", { class: "cat-row-main" }, [input, status]),
    save,
    el("button", { type: "button", text: "やめる", onclick: () => editing.replaceWith(categoryRow(node)) }),
  ]);
  row.replaceWith(editing);
  input.focus();
  input.select();
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); save.click(); }
    if (ev.key === "Escape") { ev.preventDefault(); editing.replaceWith(categoryRow(node)); }
  });
}

function paintCategoryManager() {
  const body = catDialog.querySelector("[data-ref=catBody]");
  if (!tree.length) {
    body.replaceChildren(el("p", { class: "empty", text: "カテゴリがまだありません" }));
    return;
  }
  body.replaceChildren(...tree.map(categoryRow));
}

async function refreshAll() {
  await Promise.all([loadCategories(), loadTags()]);
  await reload();
  paintCategoryManager();
  paintEntryCount(document.getElementById("count"));
}

async function removeCategory(node) {
  const where = node.scope === "local" ? "このフォルダの辞書の" : "";
  if (!confirm(`${where}カテゴリ「${node.category}」を削除します。よろしいですか？`)) return;
  try {
    // **スコープを必ず渡す。** 渡さないと、同名のグローバルのカテゴリが消える
    await api(
      `/api/categories/${encodeURIComponent(node.category)}?scope=${node.scope}`,
      { method: "DELETE" }
    );
    await refreshAll();
  } catch (err) {
    alert(`削除できません: ${err.message}`);
  }
}

async function addCategory() {
  const input = catDialog.querySelector("[data-ref=catNew]");
  const status = catDialog.querySelector("[data-ref=catStatus]");
  const scopeSel = catDialog.querySelector("[data-ref=catScope]");
  const name = input.value.trim();
  if (!name) {
    input.focus();
    return;
  }
  // **スコープを必ず渡す。** 渡さないと、フォルダのカテゴリのつもりが
  // 全体のマスターに残る（削除と同じ形の事故）
  const scope = scopeSel.hidden ? "global" : scopeSel.value;
  setStatus(status, "登録中", "busy");
  try {
    await api(`/api/categories?scope=${scope}`, { method: "POST", body: { name } });
    input.value = "";
    setStatus(status, `「${name}」を登録しました`);
    await refreshAll();
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

/**
 * フォルダの辞書が使えるときだけ、作る先を選ばせる。
 *
 * 使えないときに選択肢を出すと、選んでからサーバに断られる。
 */
async function paintScopeChoice() {
  const scopeSel = catDialog.querySelector("[data-ref=catScope]");
  let available = false;
  try {
    available = Boolean((await api("/api/health")).local_available);
  } catch {
    available = false;
  }
  scopeSel.hidden = !available;
  if (!available) scopeSel.value = "global";
}

// ------------------------------------------------------------------- 起動

/**
 * 辞書一覧を ``host`` に描く。ページとして開いたときも、ビューアに重ねる
 * ときも、ここを通る。
 */
export async function mount(container, { search = "" } = {}) {
  host = container;
  host.innerHTML = TEMPLATE;
  list = $("list");
  qInput = $("q");
  catFilter = $("catFilter");
  tagFilter = $("tagFilter");
  groupBy = $("groupBy");
  catDialog = $("catDialog");
  tree = [];
  // **前に開いたときの一覧を持ち越さない**（覆いは何度でも開き直される）
  lastEntries = null;

  // 一覧の要約に出てきた知らない語も、その場で選んで登録できるようにする
  // （ビューア・用語ページと同じ口。ここだけ「新規登録」ボタンからしか入れなかった）
  installSelectionAdd({
    root: list,
    source: () => "辞書一覧",
    onSaved: async () => {
      invalidatePopupCache();
      await refreshAll();
    },
  });

  // **listener は最初の await より前に付ける**（その間の操作を落とさない）
  qInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(reload, 180);
  });
  catFilter.addEventListener("change", reload);
  tagFilter.addEventListener("change", reload);
  // 束ね方は覚える（覆いは何度でも開き直されるので、毎回選び直させない）。
  // **サーバへは行き直さない** —— 同じものを束ね直すだけ
  try {
    const saved = localStorage.getItem(GROUP_KEY);
    if (saved === "reading" || saved === "category") groupBy.value = saved;
  } catch {
    /* 使えない環境でも既定で動く */
  }
  groupBy.addEventListener("change", () => {
    try {
      localStorage.setItem(GROUP_KEY, groupBy.value);
    } catch {
      /* 使えない環境でも、その画面では効く */
    }
    if (lastEntries) paint(lastEntries);
  });
  installBooklet();
  $("add").addEventListener("click", onAdd);
  $("manageCats").addEventListener("click", onManageCats);
  catDialog.querySelector("[data-ref=catAdd]").addEventListener("click", addCategory);
  catDialog.querySelector("[data-ref=catNew]").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      addCategory();
    }
  });
  catDialog.querySelector("[data-ref=catClose]").addEventListener("click", () => catDialog.close());

  const initial = new URLSearchParams(search);
  qInput.value = initial.get("q") || "";
  paintEntryCount(document.getElementById("count"));

  ready = Promise.all([loadCategories(), loadTags()]).then(() => {
    // `?category=` はスコープを持たないことがある（用語ページからのリンクなど）。
    // `?scope=` があればそれで、無ければ最初に見つかったものに合わせる
    const cat = initial.get("category");
    if (cat) {
      const wanted = initial.get("scope");
      const hit = tree.find((n) => n.category === cat && (!wanted || n.scope === wanted));
      if (hit) catFilter.value = `${hit.scope}/${hit.category}`;
    }
    const tag = initial.get("tag");
    if (tag) tagFilter.value = tag;
    return reload();
  });
  await ready;
}

async function onAdd() {
  const saved = await openEntryEditor({});
  if (saved) await refreshAll();
}

async function onManageCats() {
  // 開いた時点で持っているものを描き、**ダイアログを開いてから** await する
  // （開く前に待つと、その間のクリックが黙って無視される）
  paintCategoryManager();
  catDialog.showModal();
  paintScopeChoice();
  // **読み込みが終わる前に開かれることがある。** `tree` を描くだけで描き直さない
  // 作りだと、その場合「カテゴリがまだありません」のまま固まる（読み込みが
  // 遅いときだけ起きるので気付きにくい）
  await ready;
  paintCategoryManager();
}
