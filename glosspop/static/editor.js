// 辞書エントリの登録 / 編集ダイアログ。ビューアと辞書ページの両方から使う。
import { api, defaultSpoiler, el, esc, isHttpUrl, rememberSpoiler, setStatus } from "./base.js";
import { invalidatePopupCache } from "./popup.js";

const LIST_FIELDS = ["aliases", "related", "tags"];
const NEW_CATEGORY = "/new";  // "/" はカテゴリ名で禁止なので実名と衝突しない番兵

let dialog = null;
let refs = {};
let tree = [];
/** ユーザーが自分でカテゴリを選んだか。触っていなければ AI の提案を採る。 */
let categoryTouched = false;

function build() {
  if (dialog) return dialog;

  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2 data-ref="title">用語を辞書に登録</h2>
        <div class="spacer"></div>
        <button type="button" class="ghost" data-ref="cancelX" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <blockquote class="ctx-quote" data-ref="ctx" hidden></blockquote>
        <p class="notice" data-ref="notice" hidden></p>
        <div class="field-row">
          <label class="field"><span>用語 *</span>
            <input type="text" data-ref="term" required autocomplete="off"></label>
          <label class="field"><span>読み (かな)</span>
            <input type="text" data-ref="reading" autocomplete="off"></label>
        </div>
        <label class="field"><span>別名・表記ゆれ (カンマ区切り)</span>
          <input type="text" data-ref="aliases" autocomplete="off"></label>
        <div class="field-row">
          <label class="field"><span>カテゴリ</span>
            <select data-ref="category"></select>
            <input type="text" data-ref="newCategory" class="gap-top" hidden
                   placeholder="新しいカテゴリ名" autocomplete="off"
                   aria-label="新しいカテゴリ名">
            <p class="hint" data-ref="categoryHint"></p></label>
          <label class="field"><span>サブカテゴリ</span>
            <input type="text" data-ref="subcategory" list="gp-subs" autocomplete="off">
            <datalist id="gp-subs"></datalist></label>
        </div>
        <label class="field"><span>保存先</span>
          <select data-ref="scope">
            <option value="auto">自動（AI が選ぶ）</option>
            <option value="global">全体の辞書（どの文書でも有効）</option>
            <option value="local">このフォルダだけ</option>
          </select>
          <p class="hint" data-ref="scopeHint"></p></label>
        <label class="field"><span>要約 (吹き出しに出る 1〜2 文)</span>
          <textarea data-ref="summary" rows="2"></textarea></label>
        <label class="field"><span>本文 (Markdown)</span>
          <textarea data-ref="definition" rows="9"></textarea></label>
        <div class="field-row">
          <label class="field"><span>使用例 (1 行 1 件)</span>
            <textarea data-ref="examples" rows="3"></textarea></label>
          <div>
            <label class="field"><span>関連語 (カンマ区切り)</span>
              <input type="text" data-ref="related" autocomplete="off"></label>
            <label class="field"><span>タグ (カンマ区切り)</span>
              <input type="text" data-ref="tags" autocomplete="off"></label>
          </div>
        </div>
        <label class="field"><span>出典</span>
          <input type="text" data-ref="source" autocomplete="off">
          <p class="hint"><a data-ref="sourceLink" class="ext-link" target="_blank"
             rel="noreferrer noopener" hidden>出典を開く</a></p></label>
      </div>
      <footer>
        <button type="button" data-ref="draft">✨ AI で下書き</button>
        <select data-ref="spoiler" class="auto-width" aria-label="ネタバレ"
                title="AI にどこまで読ませるか">
          <option value="position">初出位置だけ（AI を使わない）</option>
          <option value="first">初出の場面だけで書く</option>
          <option value="full">全文から書く（ネタバレ可）</option>
        </select>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="cancel">キャンセル</button>
        <button type="button" class="primary" data-ref="save">保存</button>
      </footer>
    </form>`;

  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;

  refs.category.addEventListener("change", () => {
    categoryTouched = true;
    const creating = refs.category.value === NEW_CATEGORY;
    refs.newCategory.hidden = !creating;
    if (creating) refs.newCategory.focus();
    paintSubcategories();
  });
  refs.newCategory.addEventListener("input", () => {
    categoryTouched = true;
  });
  refs.source.addEventListener("input", paintSourceLink);

  document.body.append(dialog);
  return dialog;
}

/** 保存先 (自動 / 全体 / このフォルダ) の選択肢を整える。編集中は動かせない。 */
async function paintScope(selected, editing) {
  // 編集中は実際の保存先を出す。新規は既定で AI に選ばせる
  refs.scope.value = editing ? (selected === "local" ? "local" : "global") : (selected || "auto");
  refs.scope.disabled = editing;
  try {
    const health = await api("/api/health");
    refs.scopeHint.textContent = editing
      ? "保存先は変えられません（登録し直してください）"
      : `このフォルダ = ${health.content_dir}`;
  } catch {
    refs.scopeHint.textContent = "";
  }
}

/** マスターを読み直してカテゴリ選択肢を作る。 */
async function loadCategories(selected) {
  try {
    tree = await api("/api/categories");
  } catch {
    tree = [];
  }
  const options = tree.map((node) =>
    el("option", {
      value: node.category,
      text: node.count ? `${node.category} (${node.count})` : node.category,
    })
  );
  options.push(el("option", { value: NEW_CATEGORY, text: "＋ 新しいカテゴリ…" }));
  refs.category.replaceChildren(...options);

  const known = tree.some((n) => n.category === selected);
  if (selected && !known) {
    // マスターに無いカテゴリ (手書きファイル等) も選べるようにしておく
    refs.category.prepend(el("option", { value: selected, text: selected }));
  }
  refs.category.value = selected || tree[0]?.category || NEW_CATEGORY;
  refs.newCategory.hidden = refs.category.value !== NEW_CATEGORY;
  paintSubcategories();
}

function paintSubcategories() {
  const node = tree.find((n) => n.category === refs.category.value);
  const names = (node?.subcategories || []).map((s) => s.name).filter(Boolean);
  dialog.querySelector("#gp-subs").replaceChildren(
    ...names.map((name) => el("option", { value: name }))
  );
}

/** 出典が URL のときだけ「出典を開く」を出す。 */
function paintSourceLink() {
  const value = refs.source.value.trim();
  const ok = isHttpUrl(value);
  refs.sourceLink.hidden = !ok;
  if (ok) {
    refs.sourceLink.href = value;
    refs.sourceLink.title = value;
  } else {
    refs.sourceLink.removeAttribute("href");
  }
}

function currentCategory() {
  return refs.category.value === NEW_CATEGORY
    ? refs.newCategory.value.trim()
    : refs.category.value;
}

/** 初出位置はフォームに出さないが、保存時に落とさないよう保持する。 */
let firstSeen = { first_file: "", first_locator: "" };

function readForm() {
  const listOf = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);
  const draft = {
    ...firstSeen,
    term: refs.term.value.trim(),
    reading: refs.reading.value.trim(),
    // 「自動」のまま保存されることもある (AI を使わずに手で書いた場合)。
    // サーバに auto は無いので、その場合は全体の辞書にする
    scope: refs.scope.value === "auto" ? "global" : refs.scope.value,
    category: currentCategory(),
    subcategory: refs.subcategory.value.trim(),
    summary: refs.summary.value.trim(),
    definition: refs.definition.value.trim(),
    examples: refs.examples.value.split("\n").map((s) => s.trim()).filter(Boolean),
    source: refs.source.value.trim(),
  };
  for (const f of LIST_FIELDS) draft[f] = listOf(refs[f].value);
  return draft;
}

function writeForm(data) {
  firstSeen = {
    first_file: data.first_file || "",
    first_locator: data.first_locator || "",
  };
  refs.term.value = data.term || "";
  refs.reading.value = data.reading || "";
  refs.subcategory.value = data.subcategory || "";
  refs.summary.value = data.summary || "";
  refs.definition.value = data.definition || "";
  refs.examples.value = (data.examples || []).join("\n");
  refs.source.value = data.source || "";
  for (const f of LIST_FIELDS) refs[f].value = (data[f] || []).join(", ");
  paintSourceLink();
}

function setNotice(html) {
  refs.notice.hidden = !html;
  refs.notice.innerHTML = html || "";
}

/**
 * ダイアログを開く。
 *
 * @param {object} o
 * @param {string} [o.term]     初期の用語 (選択テキスト)
 * @param {string} [o.context]  用語が現れた文脈 (AI 下書きに渡す)
 * @param {string} [o.source]   出典
 * @param {string} [o.ref]      指定すると編集モード ("カテゴリ/slug")
 * @param {object} [o.entry]    編集モードの初期値
 * @returns {Promise<object|null>} 保存されたエントリ、キャンセルなら null
 */
export async function openEntryEditor({
  term = "",
  context = "",
  source = "",
  ref = null,
  entry = null,
  scope = "",
} = {}) {
  build();
  let targetRef = ref;
  categoryTouched = Boolean(entry?.category);
  await paintScope(entry?.scope || scope, Boolean(targetRef));
  refs.spoiler.value = await defaultSpoiler();

  refs.title.textContent = targetRef ? "用語を編集" : "用語を辞書に登録";
  refs.save.textContent = targetRef ? "更新" : "保存";
  refs.draft.hidden = Boolean(targetRef) && Boolean(entry?.definition);
  writeForm(entry || { term, source });
  setStatus(refs.status, "");
  setNotice("");
  refs.categoryHint.textContent = targetRef
    ? "変えるとファイルごと移動します"
    : "同じ用語でもカテゴリが違えば別に登録できます";

  if (context) {
    refs.ctx.hidden = false;
    refs.ctx.textContent = context.length > 600 ? context.slice(0, 600) + "…" : context;
  } else {
    refs.ctx.hidden = true;
  }

  await loadCategories(entry?.category || "");

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      cleanup();
      dialog.close();
      resolve(value);
    };

    const onDraft = async () => {
      const t = refs.term.value.trim();
      if (!t) {
        setStatus(refs.status, "用語を入力してください", "error");
        refs.term.focus();
        return;
      }
      refs.draft.disabled = refs.save.disabled = true;
      setStatus(refs.status, "Claude が下書きを作成中 (数十秒かかります)", "busy");
      try {
        rememberSpoiler(refs.spoiler.value);
        const origin = refs.source.value.trim() || source;
        const res = await api("/api/ai/draft", {
          method: "POST",
          body: {
            term: t,
            context,
            source: origin,
            spoiler: refs.spoiler.value,
            // 出典が content 内の相対パスなら、サーバが初出位置を数えられる
            file: isHttpUrl(origin) ? "" : origin,
            // ローカル辞書に入れるつもりなら、提案カテゴリでマスターを汚さない
            scope: refs.scope.value,
          },
        });
        // ユーザーが手で書いた内容は消さない
        const typed = readForm();
        const merged = { ...res.draft };
        for (const [k, v] of Object.entries(typed)) {
          if (k === "category") continue;
          if (Array.isArray(v) ? v.length : v) merged[k] = v;
        }
        merged.summary = res.draft.summary || merged.summary;
        merged.definition = res.draft.definition || merged.definition;
        writeForm(merged);
        // AI が選んだ保存先を見せる (以後はそれが選択値になる)
        if (refs.scope.value === "auto" && res.draft.scope) {
          refs.scope.value = res.draft.scope;
          refs.scopeHint.textContent =
            res.draft.scope === "local"
              ? "AI の判断: この資料の中だけで通じる語 → このフォルダだけ"
              : "AI の判断: ほかの文書でも通じる語 → 全体の辞書";
        }
        // AI が新しいカテゴリを提案していればマスターに登録済みなので読み直す。
        // 自分でカテゴリを選んでいた場合だけ、その選択を優先する
        await loadCategories(
          categoryTouched ? typed.category : (res.draft.category || typed.category || "")
        );
        refs.subcategory.value = merged.subcategory || "";

        const inSame = (res.existing || []).find((e) => e.category === currentCategory());
        if (inSame && !targetRef) {
          targetRef = inSame.ref;
          refs.title.textContent = "用語を編集 (このカテゴリに登録済み)";
          refs.save.textContent = "上書き更新";
        }
        const notices = [];
        if (res.warning) notices.push(esc(res.warning));
        if (res.existing?.length) {
          const list = res.existing.map((e) => `<strong>${esc(e.path_label)}</strong>`).join("、");
          notices.push(
            inSame
              ? `このカテゴリに既にあります（${list}）。更新すると上書きします。`
              : `同じ表記が ${list} に登録済みです。別カテゴリなので、このまま登録できます。`
          );
        }
        setNotice(notices.join("<br>"));
        setStatus(refs.status, "下書きができました。確認して保存してください。");
      } catch (err) {
        setStatus(refs.status, err.message, "error");
      } finally {
        refs.draft.disabled = refs.save.disabled = false;
      }
    };

    const onSave = async () => {
      const draft = readForm();
      if (!draft.term) {
        setStatus(refs.status, "用語は必須です", "error");
        refs.term.focus();
        return;
      }
      if (!draft.category) {
        setStatus(refs.status, "カテゴリを選ぶか、新しい名前を入力してください", "error");
        (refs.newCategory.hidden ? refs.category : refs.newCategory).focus();
        return;
      }
      refs.save.disabled = refs.draft.disabled = true;
      setStatus(refs.status, "保存中", "busy");
      try {
        const saved = targetRef
          ? await api(`/api/entries/${encodePath(targetRef)}`, { method: "PUT", body: draft })
          : await api("/api/entries", { method: "POST", body: draft });
        invalidatePopupCache();
        finish(saved);
      } catch (err) {
        setStatus(refs.status, err.message, "error");
        refs.save.disabled = refs.draft.disabled = false;
      }
    };

    const onCancel = () => finish(null);
    // 単一行入力での Enter と Ctrl/Cmd+Enter をどちらも保存にする
    const onSubmit = (ev) => {
      ev.preventDefault();
      onSave();
    };
    const onKey = (ev) => {
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        onSave();
      }
    };
    const onClose = () => finish(null);

    function cleanup() {
      refs.draft.removeEventListener("click", onDraft);
      refs.save.removeEventListener("click", onSave);
      refs.cancel.removeEventListener("click", onCancel);
      refs.cancelX.removeEventListener("click", onCancel);
      refs.form.removeEventListener("submit", onSubmit);
      dialog.removeEventListener("keydown", onKey);
      dialog.removeEventListener("close", onClose);
    }

    refs.draft.addEventListener("click", onDraft);
    refs.save.addEventListener("click", onSave);
    refs.cancel.addEventListener("click", onCancel);
    refs.cancelX.addEventListener("click", onCancel);
    refs.form.addEventListener("submit", onSubmit);
    dialog.addEventListener("keydown", onKey);
    dialog.addEventListener("close", onClose);

    refs.save.disabled = refs.draft.disabled = false;
    dialog.showModal();
    refs.term.focus();
  });
}

/** "カテゴリ/slug" を、区切りの / は残したままエンコードする。 */
export function encodePath(ref) {
  return String(ref).split("/").map(encodeURIComponent).join("/");
}
