// 辞書エントリの登録 / 編集ダイアログ。ビューアと辞書ページの両方から使う。
import { api, el, fillCategoryDatalists, setStatus } from "./base.js";
import { invalidatePopupCache } from "./popup.js";

const LIST_FIELDS = ["aliases", "related", "tags"];

let dialog = null;
let refs = {};

function build() {
  if (dialog) return dialog;

  dialog = el("dialog", { class: "sheet" });
  dialog.innerHTML = `
    <form data-ref="form" novalidate>
      <header>
        <h2 data-ref="title">用語を辞書に登録</h2>
        <div class="spacer" style="flex:1"></div>
        <button type="button" class="ghost" data-ref="cancelX" aria-label="閉じる">✕</button>
      </header>
      <div class="body">
        <blockquote class="ctx-quote" data-ref="ctx" hidden></blockquote>
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
            <input type="text" data-ref="category" list="gp-cats" autocomplete="off">
            <datalist id="gp-cats"></datalist></label>
          <label class="field"><span>サブカテゴリ</span>
            <input type="text" data-ref="subcategory" list="gp-subs" autocomplete="off">
            <datalist id="gp-subs"></datalist></label>
        </div>
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
          <input type="text" data-ref="source" autocomplete="off"></label>
      </div>
      <footer>
        <button type="button" data-ref="draft">✨ AI で下書き</button>
        <span class="status" data-ref="status"></span>
        <span class="spacer"></span>
        <button type="button" data-ref="cancel">キャンセル</button>
        <button type="button" class="primary" data-ref="save">保存</button>
      </footer>
    </form>`;

  refs = {};
  for (const node of dialog.querySelectorAll("[data-ref]")) refs[node.dataset.ref] = node;
  document.body.append(dialog);
  return dialog;
}

function readForm() {
  const listOf = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);
  const draft = {
    term: refs.term.value.trim(),
    reading: refs.reading.value.trim(),
    category: refs.category.value.trim(),
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
  refs.term.value = data.term || "";
  refs.reading.value = data.reading || "";
  refs.category.value = data.category || "";
  refs.subcategory.value = data.subcategory || "";
  refs.summary.value = data.summary || "";
  refs.definition.value = data.definition || "";
  refs.examples.value = (data.examples || []).join("\n");
  refs.source.value = data.source || "";
  for (const f of LIST_FIELDS) refs[f].value = (data[f] || []).join(", ");
}

/**
 * ダイアログを開く。
 *
 * @param {object} o
 * @param {string} [o.term]     初期の用語 (選択テキスト)
 * @param {string} [o.context]  用語が現れた文脈 (AI 下書きに渡す)
 * @param {string} [o.source]   出典
 * @param {string} [o.slug]     指定すると編集モード
 * @param {object} [o.entry]    編集モードの初期値
 * @param {boolean} [o.autoDraft] 開いた直後に AI 下書きを走らせる
 * @returns {Promise<object|null>} 保存されたエントリ、キャンセルなら null
 */
export function openEntryEditor({ term = "", context = "", source = "", slug = null, entry = null, autoDraft = false } = {}) {
  build();
  let targetSlug = slug;

  refs.title.textContent = targetSlug ? "用語を編集" : "用語を辞書に登録";
  refs.save.textContent = targetSlug ? "更新" : "保存";
  refs.draft.hidden = Boolean(targetSlug) && Boolean(entry?.definition);
  writeForm(entry || { term, source });
  setStatus(refs.status, "");

  if (context) {
    refs.ctx.hidden = false;
    refs.ctx.textContent = context.length > 600 ? context.slice(0, 600) + "…" : context;
  } else {
    refs.ctx.hidden = true;
  }

  fillCategoryDatalists(
    dialog.querySelector("#gp-cats"),
    dialog.querySelector("#gp-subs"),
  ).catch(() => {});

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
        const res = await api("/api/ai/draft", {
          method: "POST",
          body: { term: t, context, source: refs.source.value.trim() || source },
        });
        // ユーザーが手で書いた内容は消さない
        const merged = { ...res.draft };
        for (const [k, v] of Object.entries(readForm())) {
          if (Array.isArray(v) ? v.length : v) merged[k] = v;
        }
        merged.summary = res.draft.summary || merged.summary;
        merged.definition = res.draft.definition || merged.definition;
        writeForm(merged);
        if (res.existing_slug && !targetSlug) {
          targetSlug = res.existing_slug;
          refs.title.textContent = "用語を編集 (既に登録済み)";
          refs.save.textContent = "上書き更新";
          setStatus(refs.status, `「${merged.term}」は既に登録済みです。更新すると上書きします。`, "error");
        } else {
          setStatus(refs.status, "下書きができました。確認して保存してください。");
        }
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
      refs.save.disabled = refs.draft.disabled = true;
      setStatus(refs.status, "保存中", "busy");
      try {
        const saved = targetSlug
          ? await api(`/api/entries/${encodeURIComponent(targetSlug)}`, { method: "PUT", body: draft })
          : await api("/api/entries", { method: "POST", body: draft });
        invalidatePopupCache(saved.slug);
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
    if (autoDraft && !targetSlug) onDraft();
  });
}
