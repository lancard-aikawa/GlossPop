# GlossPop

Markdown / テキストをブラウザで読みながら、**知らない言葉を選択するだけで辞書に登録**できるビューア。
一度登録した語は以後の表示で自動的にリンクになり、ホバーで吹き出しが出て、そこから辞書ページへ飛べる。

```
テキスト表示 → 語を選択 → AI が下書き → 保存 → 以後は自動リンク＋吹き出し
```

## セットアップ

```powershell
uv sync
uv run glosspop serve
```

http://127.0.0.1:8765/ が開ける状態になる。

AI 下書きは Claude Code CLI (`claude`) をサブプロセスで呼ぶ。PATH に `claude` があれば
それだけで動く（API キーは不要）。見つからない場合も手動入力での登録はできる。

## 使いかた

### ビューアで登録する

1. 左パネルから `content/` のファイルを選ぶ、ファイルを開く、ウィンドウに .md をドロップする、
   またはテキストを貼り付けて **表示** を押す
2. 本文中の語を選択すると **＋ 辞書に登録** ボタンが出る
3. ダイアログで **✨ AI で下書き** を押すと、選択語とその前後の文脈を Claude に渡して
   読み・別名・カテゴリ・要約・本文の案を作る（数十秒かかる）
4. 内容を直して **保存**。本文が再描画され、その語がリンクになる

これは**辞書ページの本文でも同じように使える**。辞書を読んでいて出てきた別の知らない語を、
そのまま選択して登録できる（保存すると本文が再描画され、新しい語がリンクになる）。
すでに登録済みの語を選択した場合は、409 で弾く代わりにそのエントリの編集ダイアログが開く。

リンクの挙動:

| 操作 | 結果 |
| --- | --- |
| ホバー / フォーカス | 吹き出しを表示 |
| クリック | 吹き出しを固定表示（Esc で閉じる） |
| Ctrl / ⌘ + クリック | 辞書ページを新しいタブで開く |
| 吹き出しの「辞書ページを開く →」 | 辞書ページへ移動 |

### Claude Code から登録する

`.claude/skills/gloss-add/` にスキルを同梱している。

```
/gloss-add 結果整合性
```

会話の流れやコードから用語を拾って登録したいときはこちら。中では CLI を使っている。

```powershell
uv run glosspop list --json               # 一覧
uv run glosspop categories                # カテゴリ構成
uv run glosspop show 冪等                  # 1 語の Markdown を表示
uv run glosspop add --json @entry.json    # 登録（--update で上書き）
uv run glosspop rm 冪等                    # 削除
```

## データの形

1 用語 = 1 Markdown ファイル。`data/glossary/<slug>.md`。

```markdown
---
term: 冪等
reading: べきとう
aliases:
  - idempotent
category: プログラミング
subcategory: API
summary: 何度実行しても結果が同じであること。
examples:
  - PUT は冪等なのでリトライしても安全。
tags:
  - 設計原則
created_at: '2026-07-30T10:00:00+09:00'
updated_at: '2026-07-30T10:00:00+09:00'
---

同じ操作を繰り返しても状態が変わらない性質。...
```

- 本文がそのまま定義文になる。エディタで直接編集してよい（サーバは mtime を見て読み直す）
- `slug` はファイル名が正。frontmatter には書かない
- `aliases` に書いた表記も自動リンクの対象になる

### 本文の改行

辞書本文は **保存時に 1 文 1 行へ整えられる**（60 字を超える行を句点で割る）。AI は 1 段落に
5〜6 文を詰め込みがちで、そのまま出すと読みづらい壁になるため。副作用として git の差分も
文単位になる。表示側は単一改行を `<br>` にするので、書いたとおりの改行で出る。

コードフェンス・見出し・表・インラインコードの中は割らない。空行で区切った段落構成は保たれる。

**ビューアで開くソース文書のほうは触らない** — 標準の CommonMark どおり、単一改行は改行に
ならない。自分の .md が勝手に整形されることはない。

## 自動リンクの規則

- 日本語は語境界が無いので**部分文字列マッチ**。英数字で始まる/終わる語だけ前後の
  英数字境界をチェックする（`API` は `APIs` にマッチしない）
- 同じ位置では**長い表記が優先**（`機械学習` が登録されていれば `学習` は使われない）
- 大文字小文字は区別しない。本文の表記はそのまま残る
- `<a> <code> <pre> <script> <style> <textarea> <kbd> <samp>` の中身は対象外
- 表示オプションで「各用語の最初の 1 回だけリンク」に切り替えられる

一般的すぎる語（「処理」「設定」など）を `aliases` に入れると本文がリンクだらけになるので注意。

## 設定（環境変数）

| 変数 | 既定 | 用途 |
| --- | --- | --- |
| `GLOSSPOP_GLOSSARY_DIR` | `./data/glossary` | 辞書の置き場所 |
| `GLOSSPOP_CONTENT_DIR` | `./content` | ビューアがブラウズする .md / .txt |
| `GLOSSPOP_CLAUDE_BIN` | PATH から自動検出 | `claude` の場所 |
| `GLOSSPOP_CLAUDE_ARGS` | `--model sonnet` | `claude -p` に渡す追加引数 |
| `GLOSSPOP_CLAUDE_TIMEOUT` | `180` | AI 下書きのタイムアウト秒 |

## 構成

```
glosspop/
  app.py       FastAPI ルーティング
  store.py     辞書 CRUD (Markdown + frontmatter)
  linker.py    レンダリング済み HTML への自動リンク挿入
  render.py    Markdown / テキスト → HTML、本文の改行整形
  ai.py        claude CLI サブプロセス呼び出し
  cli.py       serve / add / list / show / rm / categories
  static/
    viewer.js      ビューア
    glossary.js    辞書一覧
    entry.js       用語ページ
    select-add.js  選択 → 登録 (ビューアと用語ページで共用)
    popup.js       吹き出し
    editor.js      登録 / 編集ダイアログ
.claude/skills/gloss-add/   Claude Code 用スキル
```

## テスト

```powershell
uv run pytest
```
