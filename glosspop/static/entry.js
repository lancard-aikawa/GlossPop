// 辞書の 1 語ページ。URL は /glossary/<カテゴリ>/<slug>
import {
  api, el, esc, menuButton, paintEntryCount, RANK_MARK, RANK_OPTIONS, setStatus, sourceNode,
} from "./base.js";
import { installTabKeys } from "./dict-tabs.js";
import { installGlossPopup, invalidatePopupCache } from "./popup.js";
import { installSelectionAdd } from "./select-add.js";
import { openEntryEditor, encodePath } from "./editor.js";
import { openMerge } from "./merge.js";
import { openRelationsDialog } from "./relations-draft.js";

//: 描く先。`/glossary/<カテゴリ>/<slug>` を直接開いたときはそのページの器、
//: ビューアに重ねるときは覆いの器。**`location` を直接読まない** —— 重ねると
//: 「覆いが出しているもの」と食い違う
let root = null;
let countNode = null;
let initialRef = "";
let selection = null;

//: 「関係を足す」を開いたままにしておくかの記憶。**畳んだままが既定**
const REL_FORM_KEY = "glosspop.entryRelForm";

/** 表記 -> [{ref, path_label}] の索引。関連語をリンクにするために使う。 */
let index = new Map();
/** 関係の行き先を入力するときの候補（全エントリ）。 */
let allEntries = [];
/** 表示中のエントリ。 */
let current = null;

async function loadIndex() {
  try {
    const entries = await api("/api/entries");
    allEntries = entries;
    index = new Map();
    for (const e of entries) {
      for (const s of [e.term, ...(e.aliases || [])]) {
        const key = s.toLowerCase();
        if (!index.has(key)) index.set(key, []);
        index.get(key).push(e);
      }
    }
  } catch { /* 索引が無くてもページは出す */ }
}

function chip(text, href) {
  return el("a", { class: "chip", href, text });
}

function section(title, children) {
  return el("section", { class: "entry-section" }, [el("h2", { text: title }), ...[].concat(children)]);
}


// --------------------------------------------------------------------------- //
// 関係
//
// **関係は片側にしか書かない。** 逆向きは書かせず、相手のページでは
// 「指されている側」(backlinks) として出す。両側に書けると必ずずれる。
// --------------------------------------------------------------------------- //

/** 関係 1 件の行。解決できていなければ赤リンクにする。 */
function relationRow(rel, onRemove) {
  const arrow = el("span", {
    class: "rel-arrow",
    text: rel.mutual ? "⇄" : "→",
    title: rel.mutual ? "相互" : "一方的",
  });
  const target = el("a", {
    class: rel.missing ? "chip missing" : "chip",
    href: rel.url,
    text: rel.term,
    title: rel.missing ? rel.reason : rel.path_label,
  });
  const words = [rel.label, rel.mutual && rel.back !== rel.label ? `（逆: ${rel.back}）` : ""]
    .filter(Boolean)
    .join(" ");

  const bits = [arrow, target];
  if (words) bits.push(el("span", { class: "rel-label", text: words }));
  if (rel.rank) bits.push(el("span", { class: "rel-rank", text: RANK_MARK[rel.rank] }));
  if (rel.reveal) {
    bits.push(el("span", { class: "rel-reveal", text: `判明: ${rel.reveal}`, title: "相関図では既定で伏せる" }));
  }
  // **判明位置とは別の軸**（あちらは読者がいつ知るか、こちらは作中でいつか）。
  // 書いてあるものだけ出す —— 時刻を書かない関係のほうが普通。
  // **語から継いだぶんは、継いだと分かるように出す** —— 同じ見た目で並べると
  // 「この関係に時刻を書いた覚えはない」ものが書いたように見え、消そうとして
  // 関係のほうを探し回ることになる（実際の値は語の側にある）
  if (rel.when) {
    bits.push(el("span", {
      class: rel.when_inherited ? "rel-when inherited" : "rel-when",
      text: rel.when_inherited ? `作中: ${rel.when}（語の時刻）` : `作中: ${rel.when}`,
      title: rel.when_inherited
        ? "この関係には時刻が書かれていないので、両端の語のうち遅いほうの時刻で並べます"
        : "相関図の時系列で、この順に並べます（先頭の西暦で並べ替え）",
    }));
  }
  if (rel.missing) bits.push(el("span", { class: "hint", text: rel.reason }));
  bits.push(el("span", { class: "spacer" }));
  bits.push(el("button", { type: "button", class: "ghost", text: "削除", onclick: onRemove }));
  return el("li", { class: "rel-row" }, bits);
}

/** 「この語を指している側」。書いていない側にも関係を見せるための行。 */
function backlinkRow(link) {
  const bits = [
    el("span", { class: "rel-arrow", text: link.mutual ? "⇄" : "←", title: link.mutual ? "相互" : "相手からの一方的な関係" }),
    el("a", { class: "chip", href: link.url, text: link.term, title: link.path_label }),
  ];
  if (link.label) bits.push(el("span", { class: "rel-label", text: link.label }));
  else bits.push(el("span", { class: "hint", text: `相手側に「${link.incoming}」と書かれています` }));
  if (link.rank) bits.push(el("span", { class: "rel-rank", text: RANK_MARK[link.rank] }));
  if (link.reveal) {
    bits.push(el("span", { class: "rel-reveal", text: `判明: ${link.reveal}`, title: "相関図では既定で伏せる" }));
  }
  // **継いだぶんは指されている側でも継いだと分かるように出す**（`relationRow()` と
  // 同じ見た目）。書いた時刻と同じ形で並べると、消そうとして**相手の関係のほうを
  // 探し回る** —— 実際の値は語の側にある。ここだけ落とすと、同じ 1 本の関係が
  // 書いた側と指されている側で違って見える
  if (link.when) {
    bits.push(el("span", {
      class: link.when_inherited ? "rel-when inherited" : "rel-when",
      text: link.when_inherited ? `作中: ${link.when}（語の時刻）` : `作中: ${link.when}`,
      title: link.when_inherited
        ? "この関係には時刻が書かれていないので、両端の語のうち遅いほうの時刻で並べます"
        : "相関図の時系列で、この順に並べます（先頭の西暦で並べ替え）",
    }));
  }
  return el("li", { class: "rel-row" }, bits);
}

/** 関係を書き換えて保存し、描き直す。 */
async function saveRelations(entry, relations, status) {
  setStatus(status, "保存中", "busy");
  try {
    await api(`/api/entries/${encodePath(entry.ref)}`, {
      method: "PUT",
      body: { ...entry, relations },
    });
    invalidatePopupCache();
    await reload(entry.ref);
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

/** 追加フォーム。prompt() は使わず、インラインの input / select で入力する。 */
function relationForm(entry) {
  const listId = "rel-targets";
  const datalist = el(
    "datalist",
    { id: listId },
    allEntries
      .filter((e) => e.ref !== entry.ref)
      .map((e) => el("option", { value: e.term, label: e.path_label }))
  );
  const to = el("input", {
    type: "text",
    list: listId,
    placeholder: "相手の用語名 または カテゴリ/slug",
    "aria-label": "関係の相手",
  });
  const label = el("input", { type: "text", placeholder: "この語から見た一言（例: 親友）", "aria-label": "関係" });
  const back = el("input", { type: "text", placeholder: "逆から見た一言（空なら一方的）", "aria-label": "逆からの関係" });
  const rank = el(
    "select",
    { class: "auto-width", "aria-label": "上下" },
    RANK_OPTIONS.map(([value, text]) => el("option", { value, text }))
  );
  const reveal = el("input", {
    type: "text",
    class: "narrow",
    placeholder: "判明する位置（任意）",
    "aria-label": "判明する位置",
    title: "書いておくと相関図では既定で伏せられます",
  });
  // **判明する位置とは別の軸。** 空でよい（時刻を書かない関係のほうが普通）
  const when = el("input", {
    type: "text",
    class: "narrow",
    placeholder: "作中の時刻（任意）",
    "aria-label": "作中の時刻",
    title: "例: 1560-05-19 永禄三年五月十九日（先頭の西暦で並べます）",
  });
  const status = el("span", { class: "status" });

  const add = el("button", {
    type: "button",
    class: "primary",
    text: "関係を足す",
    onclick: async () => {
      if (!to.value.trim()) {
        setStatus(status, "相手を入力してください", "error");
        to.focus();
        return;
      }
      const next = [
        ...(entry.relations || []),
        {
          to: to.value,
          label: label.value,
          back: back.value,
          rank: rank.value,
          reveal: reveal.value,
          when: when.value,
        },
      ];
      await saveRelations(entry, next, status);
    },
  });

  return el("div", { class: "rel-form" }, [
    datalist,
    el("div", { class: "rel-form-line" }, [to, label, back]),
    el("div", { class: "rel-form-line" }, [rank, reveal, when, add, status]),
    el("p", {
      class: "hint",
      text:
        "すべて「この語から見た相手」の向きで書きます。逆から見た一言を入れると相互（⇄）、" +
        "空なら一方的（→）になります。相手側に同じ関係を書く必要はありません。",
    }),
  ]);
}

// --------------------------------------------------------------------------- //
// この語が出てくる文書
//
// 探すのは**開いているフォルダ**で、押したときに読む（索引を持たない）。
// 用語ページを開くたびに全文書を読ませないよう、`<details>` を開いた時点で
// 初めて呼ぶ。どのフォルダを読んだかは必ず出す —— 出さないと「無い」のか
// 「別のフォルダを見ている」のか区別が付かない。
// --------------------------------------------------------------------------- //

/** 使用例に 1 文足す。すでに入っていれば何もしない。 */
async function addExample(entry, sentence, status) {
  const examples = [...(entry.examples || [])];
  if (examples.includes(sentence)) {
    setStatus(status, "その文はもう使用例に入っています");
    return;
  }
  setStatus(status, "保存中", "busy");
  try {
    await api(`/api/entries/${encodePath(entry.ref)}`, {
      method: "PUT",
      body: { ...entry, examples: [...examples, sentence] },
    });
    invalidatePopupCache();
    await reload(entry.ref);
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

function appearanceFile(entry, file, status) {
  const query = new URLSearchParams({ open: file.path, term: entry.term });
  const rows = file.hits.map((hit) =>
    el("li", { class: "rel-row" }, [
      el("span", { class: "rel-rank", text: hit.locator }),
      el("span", { class: "rel-label", text: hit.sentence || hit.snippet }),
      el("span", { class: "spacer" }),
      // AI を呼ばずに使用例が埋まる経路。文の切れ目まで採ってある
      el("button", {
        type: "button",
        class: "ghost",
        text: "使用例に足す",
        title: "この文を使用例に追加する",
        onclick: () => addExample(entry, hit.sentence || hit.snippet, status),
      }),
    ])
  );
  if (file.count > file.hits.length) {
    rows.push(el("li", { class: "hint", text: `ほか ${file.count - file.hits.length} 件` }));
  }
  return el("div", { class: "appearance-file" }, [
    // **`.rel-sub` を使い回さない。** あれは uppercase の小見出し用で、
    // ファイル名に当てると `銀河.md` が `銀河.MD` になる
    el("h3", { class: "appearance-name" }, [
      el("a", { href: `/?${query}`, text: file.title || file.name, title: file.path }),
      el("span", { class: "count", text: `${file.count} 件` }),
    ]),
    el("ul", { class: "rel-list" }, rows),
  ]);
}

async function loadAppearances(entry, body, status) {
  body.replaceChildren(el("p", { class: "hint", text: "本文を読んでいます…" }));
  try {
    const res = await api(`/api/content-search?ref=${encodeURIComponent(entry.ref)}`);
    const notes = [`「${res.root}」の ${res.files_scanned} 文書を読みました。`];
    if (res.files_truncated) notes.push("文書が多いので途中で打ち切りました。");
    if (res.hits_truncated) notes.push("ヒットが多いので途中で打ち切りました。");
    if (res.skipped.length) notes.push(`読めなかったファイルが ${res.skipped.length} 件あります。`);
    body.replaceChildren(
      ...(res.results.length
        ? res.results.map((file) => appearanceFile(entry, file, status))
        : [el("p", { class: "empty", text: "このフォルダには出てきませんでした" })]),
      el("p", { class: "hint", text: notes.join(" ") }),
      status,
    );
  } catch (err) {
    body.replaceChildren(el("p", { class: "status error", text: err.message }));
  }
}

function appearancesSection(entry) {
  const status = el("span", { class: "status" });
  const body = el("div", { class: "appearances" });
  const box = el("details", { class: "appearances-box" }, [
    el("summary", { text: "この語が出てくる文書を探す（開いているフォルダを読みます）" }),
    body,
  ]);
  // 開いた 1 回だけ読む。用語ページを開くたびに全文書を読ませない
  box.addEventListener("toggle", () => {
    if (box.open && !body.dataset.loaded) {
      body.dataset.loaded = "1";
      loadAppearances(entry, body, status);
    }
  });
  return section("本文での使われ方", box);
}

//: 地図への入口は**タブ**（`entryTabs()`）。以前はボタンで `/graph?mode=map` へ
//: 飛ばしていたが、飛んだ先には戻る道が「ブラウザの戻る」しか無かった。
//: **`map:` が書いてあるときだけ出す**のは変えていない ——
//: **形だけあって `map:` が無いときは出さない**（どの絵の話か決まらないし、
//: そちらは点検が挙げる。`shape_without_map`）。

function relationsSection(entry) {
  const resolved = entry.relations_resolved || [];
  const links = entry.backlinks || [];
  const status = el("span", { class: "status" });

  const list = el(
    "ul",
    { class: "rel-list" },
    resolved.map((rel, i) =>
      relationRow(rel, () => {
        const next = (entry.relations || []).filter((_, j) => j !== i);
        return saveRelations(entry, next, status);
      })
    )
  );

  const parts = [];
  if (resolved.length) parts.push(list);
  else parts.push(el("p", { class: "empty", text: "まだ関係が書かれていません。" }));
  if (links.length) {
    parts.push(el("h3", { class: "rel-sub", text: "この語を指している側" }));
    parts.push(el("ul", { class: "rel-list" }, links.map(backlinkRow)));
  }
  parts.push(status);
  // **入力 7 個を常時開いておかない**（→ CLAUDE.md「操作の置き場所は 5 つしかない」）。
  // 関係の一覧と下の操作が縦に離れて、書いたものを見ながら次を足せなかった。
  // **開閉は覚える** —— よく足す人には開いたままになる（横断検索と同じ扱い）
  const formFold = el("details", { class: "rel-form-fold" }, [
    el("summary", { text: "＋ 関係を足す" }),
    relationForm(entry),
  ]);
  try {
    formFold.open = localStorage.getItem(REL_FORM_KEY) === "1";
  } catch {
    /* 使えない環境では畳んだまま（既定） */
  }
  formFold.addEventListener("toggle", () => {
    try {
      if (formFold.open) localStorage.setItem(REL_FORM_KEY, "1");
      else localStorage.removeItem(REL_FORM_KEY);
    } catch {
      /* 保存できなくてもその画面では効く */
    }
  });
  parts.push(formFold);
  parts.push(
    el("p", { class: "rel-actions" }, [
      // **ここに「カテゴリ全体の図」を戻さないこと。** 関係は**その語から見た
      // 書き方**しかしていない（向きの基準は「自分から to を見て」に固定）ので、
      // その並びの下にカテゴリ全体・辞書全体への出口があると層が飛ぶ。
      // **広い範囲への出口はパンくずの側**（`辞書 / 文学`）—— カテゴリを押せば
      // その絞り込みの一覧が出て、そこのタブがそのまま「文学の図」へ渡る
      // （`dict-tabs.js` が絞り込みを持っていく）。
      // 残しているのはどれも**この語についての操作**だけ。
      //
      // **1 語ぶんの下書き。** 全体まとめての下書きはビューアにあるが、
      // 「この語だけ関係が空のまま」を埋める道がここに無かった
      el("button", {
        type: "button",
        text: "✨ この語の関係を下書き",
        title: "開いているフォルダの本文から、この語が一方の端になる関係を探す",
        onclick: async (ev) => {
          // **ボタンは最初の await より前に掴む。** `currentTarget` は配送が
          // 終わると null になり、書き込みのあとは描き直しで消えてもいる
          const button = ev.currentTarget;
          button.disabled = true;
          try {
            if (await openRelationsDialog({ ref: entry.ref, term: entry.term })) {
              invalidatePopupCache();
              await reload(entry.ref);
            }
          } finally {
            if (button.isConnected) button.disabled = false;
          }
        },
      }),
    ])
  );
  // **見出しは付けない。** タブの名前が「関係」なので、中でもう一度言うと
  // 同じ語が 2 段に並ぶだけになる（「この語を指している側」の小見出しは残す）
  return el("section", { class: "entry-section" }, parts);
}

/**
 * 用語ごとの画像。**語り手の顔とは別物**（顔は「誰が書いているか」で辞書に 1 枚）。
 *
 * **受け取る口の規則は顔と同じ**（生のバイト列で送る・ファイル名は使わない・
 * 拡張子はサーバが中身から決める）。ここが持つのは選ばせ方と文言だけ。
 * **SVG は選ばせない** —— サーバも通さないので、選べると「入れたのに断られた」になる。
 */
function imagePanel(entry) {
  const box = el("div", { class: "entry-image", "data-ref": "imagePanel" });
  const status = el("span", { class: "status" });
  const file = el("input", {
    type: "file",
    accept: "image/png,image/jpeg,image/webp,image/gif",
    "data-ref": "imageFile",
    hidden: true,
  });

  const send = async (method, body) => {
    setStatus(status, method === "DELETE" ? "消しています" : "入れています", "busy");
    try {
      const res = await fetch(
        `/api/entry-image?ref=${encodePath(entry.ref)}`,
        { method, body, headers: body ? { "Content-Type": "application/octet-stream" } : {} }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
      setStatus(status, "");
      // **吹き出しも一覧も同じ絵を出す**ので、覚えているぶんを捨てる
      invalidatePopupCache();
      await reload(entry.ref);
    } catch (err) {
      setStatus(status, err.message, "error");
    }
  };

  file.addEventListener("change", () => {
    const picked = file.files?.[0];
    file.value = "";                       // 同じファイルをもう一度選べるように
    if (picked) send("POST", picked);
  });

  if (entry.image_url) {
    box.append(el("img", { class: "entry-photo", src: entry.image_url, alt: "" }));
  }
  box.append(el("div", { class: "toolbar" }, [
    el("button", {
      type: "button",
      "data-ref": "imagePick",
      text: entry.image_url ? "画像を差し替え" : "🖼 画像を入れる",
      title: "PNG / JPEG / GIF / WebP（4 MB まで）",
      onclick: () => file.click(),
    }),
    entry.image_url ? el("button", {
      type: "button",
      class: "danger",
      "data-ref": "imageDrop",
      text: "画像を消す",
      // **確認を取る。** 画像は控えに入るが、1 件ずつ戻す口は無い
      onclick: () => confirm(`「${entry.term}」の画像を消します。よろしいですか？`)
        && send("DELETE", null),
    }) : null,
    file,
    status,
  ]));
  return box;
}

// --------------------------------------------------------------------------- //
// この語の見方（タブ）
//
// **辞書の一覧と図を同じタブ列にしたのと同じ話。** 記事とその語の図は「同じ語の
// 別の見方」なので、ボタンで別の場所へ飛ばすのをやめてタブで切り替える。
// **語は固定したまま**見方だけが変わる（飛ぶと、戻る道は「戻る」しかなかった）。
//
// 辞書ぜんぶの見方（`dict-tabs.js` の 6 つ）とは**別の列**。あちらは「辞書を
// どう見るか」、こちらは「この語をどう見るか」で、範囲が違う。
// **地図があちらの列から外れたのも同じ理由**（絵 1 枚ぶんの見方なので、範囲は
// 辞書ぜんぶではない）—— こちらの 🗺 は今までどおり。
// --------------------------------------------------------------------------- //

//: いま見ているタブ。**語が変わったら記事へ戻す**（`mount()` と `goTo()`）——
//: 別の語を開いたのに図が出ていると、どの語の図なのか分からない
let entryTab = "entry";

/**
 * この語のタブ。**地図は `map:` が書いてあるときだけ**出す。
 *
 * 辞書の見方のタブ（`dict-tabs.js`）は 1 つも隠さないが、こちらは違う ——
 * あちらは「辞書をどう見るか」でどの辞書にも 6 通りあるのに対し、こちらの地図は
 * **その語が絵に結びつけられているかどうか**で、書いていなければ出すものが無い。
 * 座標がまだ無いのは正常（「置き待ち」）なので、そのときも出す。
 */
function entryTabs(entry) {
  // **関係の一覧と「この語の図」は同じものの別の見方**（並びと絵）。隣に置くと
  // それが読めるので、辞書タブの下にぶら下げるのをやめた
  const links = (entry.relations_resolved || []).length + (entry.backlinks || []).length;
  const tabs = [
    { id: "entry", label: "辞書", hint: "この語の説明・使用例・本文での使われ方" },
    {
      id: "relations",
      // **本数を出す。** 畳んだ先に何かあるのかは、開くまで分からない
      label: links ? `関係 ${links}` : "関係",
      hint: "この語の関係の一覧（この語を指している側も出る）",
    },
    // **何つ先までかは図の側で選べる**ので、ここでは言い切らない
    // （既定は 2 つ先。→ `ego.js` の `DEPTHS`）
    { id: "ego", label: "この語の図", hint: "この語を真ん中に置いて、まわりの関係を見る" },
  ];
  if (entry.map) {
    const placed = Boolean(entry.pin?.length || entry.line?.length || entry.area?.length);
    tabs.push({
      id: "map",
      label: "🗺 地図",
      hint: placed ? `「${entry.map}」の上のこの語を見る` : `「${entry.map}」の上にこの語を置く`,
    });
  }
  return tabs;
}

/** その語の図を出すときに graph へ渡す `?...`。**名指しなので `?ref=` を使う。** */
function tabSearch(entry, tab) {
  if (tab === "map") {
    return `?${new URLSearchParams({ mode: "map", map: `${entry.scope}/${entry.map}`, ref: entry.ref })}`;
  }
  // **カテゴリで絞らない。** 中心の図が出すのは「この語の近所」なので、
  // 絞ると別のカテゴリに居る相手が落ちて近所が欠ける
  return `?ref=${encodeURIComponent(entry.ref)}`;
}

/**
 * タブの中身を描く。図は**そのとき初めて読み込む**（用語ページを開くたびに
 * 相関図のモジュールまで取りに行かない）。
 */
async function paintTabBody(entry, host, bodies) {
  if (bodies[entryTab]) {
    host.replaceChildren(...bodies[entryTab]);
    return;
  }
  host.replaceChildren(el("p", { class: "status", text: "図を描いています" }));
  try {
    const graph = await import("./graph.js");
    const box = el("div", { class: "entry-graph" });
    // **辞書全体の見方への出口。** ここに 7 つのタブを並べない —— 6 つの図のうち
    // 「その語のもの」と言えるのは中心の図と地図だけで、段の図・行列・年表は
    // **辞書全体の見方**。並べると「この語の図」と言いながら辞書全体が出る。
    // 代わりに**その語を名指ししたまま**辞書の図へ渡す（渡った先で年表にすれば、
    // `spotlight()` がその語の行を光らせる）。**図より前に置く** —— うしろだと、
    // 背の高い図をスクロールし切らないと見えない
    host.replaceChildren(
      el("p", { class: "rel-actions entry-graph-out" }, [
        el("a", {
          class: "btn",
          "data-ref": "toDictFigures",
          href: `/graph?ref=${encodeURIComponent(entry.ref)}`,
          title: "この語を選んだまま、辞書の図（段の図・行列・年表・地図…）へ渡ります",
          text: "辞書の図で見る →",
        }),
      ]),
      box,
    );
    // **辞書ぜんぶの見方のタブは出さない**（この列と二重になる）
    await graph.mount(box, { search: tabSearch(entry, entryTab), embed: true, tabs: false });
  } catch (err) {
    host.replaceChildren(el("p", { class: "status error", text: `図を出せません: ${err.message}` }));
  }
}

function render(entry) {
  current = entry;
  const head = el("div", { class: "entry-head" }, [
    // スコープも渡す。渡さないと、同名のカテゴリが全体とフォルダの両方にあるとき
    // 一覧が別の辞書のほうを選ぶ
    el("div", { class: "crumb", html:
      `<a href="/glossary">辞書</a> / ` +
      `<a href="/glossary?category=${encodeURIComponent(entry.category)}` +
      `&scope=${encodeURIComponent(entry.scope)}">${esc(entry.category)}</a>` +
      (entry.subcategory ? ` / ${esc(entry.subcategory)}` : "")
    }),
    el("h1", { html: esc(entry.term) + (entry.reading ? `<span class="reading">${esc(entry.reading)}</span>` : "") }),
  ]);
  // ペルソナ（語り手）の顔。この辞書のもの（→ docs/voices.md）
  if (entry.persona_url) {
    head.classList.add("has-face");
    head.prepend(el("img", {
      class: "entry-face", src: entry.persona_url, alt: "",
      title: `${entry.path_label} の語り手`,
    }));
  }
  if (entry.aliases?.length) {
    head.append(el("p", { class: "aliases", text: `別名: ${entry.aliases.join(" / ")}` }));
  }
  // **その語自体の作中の時刻。**別名のすぐ下（語の素性の一部で、説明ではない）。
  // 書いてある語のほうが少ないので、無いときは行ごと出さない
  if (entry.when) {
    head.append(el("p", { class: "aliases" }, [
      el("span", { class: "rel-when", text: `作中: ${entry.when}` }),
    ]));
  }
  if (entry.summary) head.append(el("p", { class: "summary", text: entry.summary }));
  if (entry.tags?.length) {
    // タグで絞り込む (`?q=` に流すと、そのタグ名が本文に出るだけの語まで拾う)
    head.append(el("div", { class: "chips" }, entry.tags.map((t) => chip(`#${t}`, `/glossary?tag=${encodeURIComponent(t)}`))));
  }
  // **用語ごとの画像は見出しの最後、本文の前**（顔は見出しの横）。**主戦場はここ**
  // —— 吹き出しは狭くて顔と取り合うので、大きく出せるのは用語ページだけ。
  // **要約とタグの間に割り込ませないこと** —— 語の説明（要約 → タグ）が
  // 押しボタンで分断されて読みにくい
  head.append(imagePanel(entry));

  const parts = [];

  // 同じ表記が別カテゴリにもあるなら案内する。
  // **「別カテゴリの同名」は正常なので、まとめろとは言わない。** 同じものが
  // 割れているのか、たまたま名前が同じ別物なのかは人にしか分からない
  const siblings = (index.get(entry.term.toLowerCase()) || []).filter((e) => e.ref !== entry.ref);
  if (siblings.length) {
    parts.push(el("p", { class: "notice" }, [
      el("span", { text: `「${entry.term}」は他のカテゴリにもあります: ` }),
      ...siblings.flatMap((e, i) => [
        i ? el("span", { text: "、" }) : null,
        el("a", { href: e.url, text: e.path_label }),
      ].filter(Boolean)),
    ]));
  }

  if (entry.definition_html) {
    parts.push(el("article", { class: "doc", html: entry.definition_html }));
  } else {
    parts.push(el("p", { class: "empty", text: "本文が未記入です。" }));
  }

  if (entry.examples_html?.length) {
    parts.push(section("使用例", el("div", { class: "doc", html: entry.examples_html.join("") })));
  }

  parts.push(appearancesSection(entry));

  const movePanel = el("div", { class: "move-panel", hidden: true });
  // 「初出へ」: ビューアでそのファイルを開き、最初の出現までスクロールする
  function firstSeenLink(e) {
    if (!e.first_file) return null;
    const query = new URLSearchParams({ open: e.first_file, term: e.term });
    return el("a", {
      href: `/?${query}`,
      text: `初出: ${e.first_file}${e.first_locator ? ` ${e.first_locator}` : ""} →`,
      title: "その場面をビューアで開く",
    });
  }

  // **外に出すのは戻せる操作だけ。** 「まとめる」と「削除」は片方のエントリが
  // 消える操作なのに、横一列に並べると編集と同じ重さに見えていた
  // （→ CLAUDE.md「消える操作は ⋯ の中で、区切り線の下に置く」）。
  // 「編集」だけ外なのは、このページでいちばん使う操作だから。
  // **この行はタブの外に置く** —— 語についての操作なので、図を出している間だけ
  // 編集できない、にはしない
  const actions = el("div", { class: "toolbar entry-actions" }, [
    el("button", {
      type: "button",
      class: "primary",
      "data-ref": "edit",
      text: "編集",
      onclick: () => edit(entry),
    }),
    menuButton({
      ref: "entryMenu",
      title: "この用語のそのほかの操作",
      items: [
        {
          label: "カテゴリを移動…",
          onSelect: () => toggleMovePanel(entry, movePanel),
        },
        { separator: true },
        {
          label: "まとめる…",
          title: "割れてしまった同じものを 1 つにする（片方が消えます）",
          danger: true,
          onSelect: () => mergeWith(entry),
        },
        {
          label: "削除…",
          danger: true,
          ref: "remove",
          onSelect: () => remove(entry),
        },
      ],
    }),
    el("span", { class: "spacer" }),
    el("a", { class: "btn", href: "/glossary", text: "一覧へ戻る" }),
  ]);

  const meta = el("p", { class: "entry-meta" });
  const bits = [
    el("span", { text: `保存先: ${entry.path}`, title: entry.path }),
    firstSeenLink(entry),
    sourceNode(entry.source),
    el("span", { text: `作成 ${entry.created_at}` }),
    el("span", { text: `更新 ${entry.updated_at}` }),
  ].filter(Boolean);
  bits.forEach((node, i) => {
    if (i) meta.append(el("span", { class: "sep", text: "·" }));
    meta.append(node);
  });

  // **見出しは動かさない。** タブで変わるのは下の中身だけで、どの語を見ているかは
  // 上に出たまま（語を固定して見方だけ変える、というのがタブにした理由そのもの）
  const tabBody = el("div", { class: "entry-tab-body" });
  const tabs = el("div", { class: "view-tabs", role: "tablist", "aria-label": "この語の見方" });
  const available = entryTabs(entry);
  // 地図タブは `map:` があるときだけなので、**消えたタブに居座らせない**
  if (!available.some((t) => t.id === entryTab)) entryTab = "entry";
  tabs.replaceChildren(...available.map((t) => el("button", {
    type: "button",
    role: "tab",
    "data-tab": t.id,
    "aria-selected": t.id === entryTab ? "true" : "false",
    tabindex: t.id === entryTab ? "0" : "-1",
    title: t.hint,
    text: t.label,
    onclick: () => {
      if (entryTab === t.id) return;
      entryTab = t.id;
      render(entry);
    },
  })));
  installTabKeys(tabs);

  // **語についての操作はタブの外。** 編集・移動・まとめる・削除・保存先は
  // 「どの見方をしているか」と関係ないので、図を出している間だけ触れない、
  // にはしない（カテゴリ移動のパネルも操作の隣に出す）
  root.replaceChildren(head, tabs, tabBody, actions, movePanel, meta);
  paintTabBody(entry, tabBody, {
    entry: parts,
    relations: [relationsSection(entry)],
  });
  document.title = `${entry.term} — GlossPop`;
}

/** サーバから引き直して描き直す (本文の自動リンクを最新にするため)。 */
async function reload(ref) {
  const target = ref || current?.ref || initialRef;
  const entry = await api(`/api/entries/${encodePath(target)}`);
  render(entry);
  return entry;
}

function goTo(ref) {
  location.href = `/glossary/${encodePath(ref)}`;
}

async function edit(entry) {
  const saved = await openEntryEditor({ ref: entry.ref, entry });
  if (!saved) return;
  invalidatePopupCache();
  selection.hide();
  if (saved.ref !== entry.ref) return goTo(saved.ref); // カテゴリ移動 / slug 変更
  await loadIndex();
  render(saved);
  paintEntryCount(countNode);
}

const NEW_CATEGORY = "/new";  // "/" はカテゴリ名で禁止なので実名と衝突しない番兵

async function toggleMovePanel(entry, panel) {
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  const tree = await api("/api/categories").catch(() => []);
  const others = tree.map((n) => n.category).filter((n) => n !== entry.category);

  const select = el("select", { class: "auto-width", "aria-label": "移動先カテゴリ" }, [
    ...others.map((n) => el("option", { value: n, text: n })),
    el("option", { value: NEW_CATEGORY, text: "＋ 新しいカテゴリ…" }),
  ]);
  const input = el("input", {
    type: "text",
    placeholder: "新しいカテゴリ名",
    "aria-label": "新しいカテゴリ名",
    hidden: others.length > 0,
  });
  if (!others.length) select.value = NEW_CATEGORY;
  select.addEventListener("change", () => {
    input.hidden = select.value !== NEW_CATEGORY;
    if (!input.hidden) input.focus();
  });

  const status = el("span", { class: "status" });
  const go = el("button", {
    type: "button",
    class: "primary",
    text: "移動",
    onclick: async () => {
      const target = (select.value === NEW_CATEGORY ? input.value : select.value).trim();
      if (!target) {
        setStatus(status, "移動先を選んでください", "error");
        return;
      }
      go.disabled = true;
      setStatus(status, "移動中", "busy");
      try {
        const moved = await api(`/api/move/${encodePath(entry.ref)}`, {
          method: "POST",
          body: { category: target },
        });
        invalidatePopupCache();
        goTo(moved.ref);
      } catch (err) {
        setStatus(status, err.message, "error");
        go.disabled = false;
      }
    },
  });

  panel.replaceChildren(
    el("span", { class: "hint", text: `現在: ${entry.category} →` }),
    select,
    input,
    go,
    el("button", { type: "button", text: "やめる", onclick: () => { panel.hidden = true; } }),
    status,
  );
  panel.hidden = false;
  select.focus();
}

async function remove(entry) {
  if (!confirm(`「${entry.term}」（${entry.path_label}）を辞書から削除します。よろしいですか？`)) return;
  try {
    await api(`/api/entries/${encodePath(entry.ref)}`, { method: "DELETE" });
    invalidatePopupCache();
    location.href = "/glossary";
  } catch (err) {
    alert(`削除できません: ${err.message}`);
  }
}

/**
 * 割れてしまった同じものを 1 つにまとめる。
 *
 * 候補として先に出すのは**同じ表記のもの**だけ。「同じ人物かもしれない」を
 * 機械で判定すると、カテゴリ違いの同名（この辞書の狙いどおりの機能）を大量に
 * 挙げてしまい、警告が誰にも読まれなくなる。あとは自分で探してもらう。
 */
async function mergeWith(entry) {
  const same = (index.get(entry.term.toLowerCase()) || []).filter((e) => e.ref !== entry.ref);
  const merged = await openMerge(entry, same);
  if (!merged) return;
  invalidatePopupCache();
  // 別名も関係も変わっているので、索引ごと引き直す
  await loadIndex();
  await reload(merged);
  paintEntryCount(countNode);
  history.replaceState(null, "", `/glossary/${encodePath(merged)}`);
}

/**
 * 用語ページを ``host`` に描く。ページとして開いたときも、ビューアに重ねる
 * ときも、ここを通る。``path`` は `/glossary/<カテゴリ>/<slug>`。
 */
export async function mount(host, { path = "" } = {}) {
  root = host;
  countNode = document.getElementById("count");   // topbar は覆いの外
  initialRef = decodeURIComponent(
    (path || location.pathname).replace(/^\/glossary\/?/, "").replace(/\/$/, "")
  );
  current = null;
  // **語が変わったら記事のタブへ戻す。** 別の語を開いたのに図が出ていると、
  // どの語の図なのか画面から決まらない（覆いは何度でも開き直される）
  entryTab = "entry";
  root.replaceChildren(el("p", { class: "empty", text: "読み込み中…" }));

  installGlossPopup();
  // 辞書ページの本文でも、知らない語を選択してそのまま登録できるようにする
  selection = installSelectionAdd({
    root,
    source: () => (current ? `辞書: ${current.term}` : "辞書"),
    onSaved: async () => {
      invalidatePopupCache();
      await Promise.all([loadIndex(), reload()]);
      paintEntryCount(countNode);
    },
  });

  paintEntryCount(countNode);
  await loadIndex();
  try {
    await reload();
  } catch (err) {
    root.replaceChildren(
      el("h1", { text: "見つかりません" }),
      el("p", { class: "status error", text: err.message }),
      el("p", { class: "hint", text: `参照: ${initialRef}` }),
      el("p", {}, [el("a", { class: "btn", href: "/glossary", text: "辞書一覧へ" })]),
    );
  }
}
