// 辞書の 1 語ページ。URL は /glossary/<カテゴリ>/<slug>
import { api, el, esc, paintEntryCount, RANK_MARK, RANK_OPTIONS, setStatus, sourceNode } from "./base.js";
import { installGlossPopup, invalidatePopupCache } from "./popup.js";
import { installSelectionAdd } from "./select-add.js";
import { openEntryEditor, encodePath } from "./editor.js";

const root = document.getElementById("root");
const countNode = document.getElementById("count");
const initialRef = decodeURIComponent(
  location.pathname.replace(/^\/glossary\/?/, "").replace(/\/$/, "")
);

installGlossPopup();

/** 表記 -> [{ref, path_label}] の索引。関連語をリンクにするために使う。 */
let index = new Map();
/** 関係の行き先を入力するときの候補（全エントリ）。 */
let allEntries = [];
/** 表示中のエントリ。 */
let current = null;

// 辞書ページの本文でも、知らない語を選択してそのまま登録できるようにする
const selection = installSelectionAdd({
  root,
  source: () => (current ? `辞書: ${current.term}` : "辞書"),
  onSaved: async () => {
    invalidatePopupCache();
    await Promise.all([loadIndex(), reload()]);
    paintEntryCount(countNode);
  },
});

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
        },
      ];
      await saveRelations(entry, next, status);
    },
  });

  return el("div", { class: "rel-form" }, [
    datalist,
    el("div", { class: "rel-form-line" }, [to, label, back]),
    el("div", { class: "rel-form-line" }, [rank, reveal, add, status]),
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
  parts.push(relationForm(entry));
  parts.push(
    el("p", {}, [
      el("a", {
        class: "btn",
        href: `/graph?category=${encodeURIComponent(entry.category)}` +
          (entry.scope === "local" ? "&scope=local" : ""),
        text: "相関図で見る →",
      }),
    ])
  );
  return section("関係", parts);
}

function render(entry) {
  current = entry;
  const head = el("div", { class: "entry-head" }, [
    el("div", { class: "crumb", html:
      `<a href="/glossary">辞書</a> / ` +
      `<a href="/glossary?category=${encodeURIComponent(entry.category)}">${esc(entry.category)}</a>` +
      (entry.subcategory ? ` / ${esc(entry.subcategory)}` : "")
    }),
    el("h1", { html: esc(entry.term) + (entry.reading ? `<span class="reading">${esc(entry.reading)}</span>` : "") }),
  ]);
  if (entry.aliases?.length) {
    head.append(el("p", { class: "aliases", text: `別名: ${entry.aliases.join(" / ")}` }));
  }
  if (entry.summary) head.append(el("p", { class: "summary", text: entry.summary }));
  if (entry.tags?.length) {
    // タグで絞り込む (`?q=` に流すと、そのタグ名が本文に出るだけの語まで拾う)
    head.append(el("div", { class: "chips" }, entry.tags.map((t) => chip(`#${t}`, `/glossary?tag=${encodeURIComponent(t)}`))));
  }

  const parts = [head];

  // 同じ表記が別カテゴリにもあるなら案内する
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
  parts.push(relationsSection(entry));

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

  parts.push(el("div", { class: "toolbar entry-actions" }, [
    el("button", { type: "button", text: "編集", onclick: () => edit(entry) }),
    el("button", {
      type: "button",
      text: "カテゴリを移動",
      onclick: () => toggleMovePanel(entry, movePanel),
    }),
    el("button", { type: "button", class: "danger", text: "削除", onclick: () => remove(entry) }),
    el("a", { class: "btn", href: "/glossary", text: "一覧へ戻る" }),
  ]));
  parts.push(movePanel);

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
  parts.push(meta);

  root.replaceChildren(...parts);
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

async function main() {
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

main();
