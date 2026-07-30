---
name: gloss-add
description: GlossPop の用語辞書に用語を登録・更新する。ユーザーが「この言葉を辞書に登録して」「用語集に追加」「GlossPop に登録」と言ったとき、または会話やコード中の専門用語を辞書化したいときに使う。カテゴリ/サブカテゴリを既存構成に揃えて 1 用語 = 1 Markdown ファイルで保存する。
---

# gloss-add — GlossPop 辞書への登録

GlossPop の辞書は `data/glossary/<slug>.md`（1 用語 = 1 ファイル、YAML frontmatter + 本文）
として保存される。ここに書いた用語は、ビューアでテキストを表示したときに自動リンクになり、
ホバーで吹き出しが出る。

## 手順

### 1. 既存を確認する

同じ用語や別名がすでに無いか、カテゴリ構成がどうなっているかを先に見る。

```bash
uv run glosspop list --json
uv run glosspop categories
uv run glosspop show "<用語名>"      # あれば内容が出る。無ければ exit 1
```

### 2. 内容を組み立てる

以下のフィールドで JSON を作る。**日本語で書く**。

| フィールド | 必須 | 内容 |
| --- | --- | --- |
| `term` | ✓ | 見出し語。表記は本文中に実際に現れる形に合わせる |
| `reading` | | 日本語の読み（かな）。ソート順に使う。英語のみの語なら空 |
| `aliases` | | 表記ゆれ・略称・英語表記。**本文中で自動リンクの対象になる**ので、同義でない語は入れない |
| `category` | ✓ | 大分類。既存カテゴリを優先して再利用する |
| `subcategory` | | 小分類。不要なら省略 |
| `summary` | ✓ | 吹き出しに出る 1〜2 文（120 字以内）。ここだけで意味が分かるように書く |
| `definition` | ✓ | 辞書ページ本文。Markdown。背景と使いどころを 3〜6 文 |
| `examples` | | 使用例（文字列の配列） |
| `related` | | 関連語。既存の `term` と一致すればリンクになる |
| `tags` | | 検索用タグ |
| `source` | | 出典（ファイル名・URL・「〇〇との会話」など） |

注意点:

- **`aliases` は慎重に。** 自動リンクは本文の部分文字列にマッチするので、
  一般的すぎる語（「処理」「設定」など）を別名に入れると本文がリンクだらけになる。
- カテゴリを新設するのは、既存のどれにも入らないときだけ。`未分類` は使わない。
- `definition` に長いコードブロックを入れない（吹き出しでは非表示になる）。

### 3. 登録する

シェルのクォートで日本語や改行が壊れないよう、**JSON はファイル経由で渡す**。

```bash
# 1) JSON を書く（Write ツールでスクラッチに作成）
#    例: /tmp/gloss-entry.json
# 2) 登録
uv run glosspop add --json @/tmp/gloss-entry.json
```

JSON の例:

```json
{
  "term": "イミュータブル",
  "reading": "いみゅーたぶる",
  "aliases": ["immutable", "不変オブジェクト"],
  "category": "プログラミング",
  "subcategory": "設計",
  "summary": "生成後に状態を変更できないデータのこと。変更したい場合は新しい値を作り直す。",
  "definition": "生成後に内部状態を変更できないデータ構造やオブジェクトを指す。...",
  "examples": ["Python の `tuple` はイミュータブルなので dict のキーにできる。"],
  "related": ["ミュータブル", "副作用"],
  "tags": ["設計原則"],
  "source": "docs/architecture.md"
}
```

出力は JSON で返る。`status` が `created` なら新規、`exists` なら既存（exit 1）。

### 4. 既存を上書きするとき

`--update` を付ける。`term` が既存エントリの `term` か `aliases` に一致するものを更新する。

```bash
uv run glosspop add --json @/tmp/gloss-entry.json --update
```

**`--update` は本文ごと置き換える。** 既存の内容を残したい場合は先に
`uv run glosspop show "<用語名>" --json` で読み、マージした JSON を作ってから渡すこと。

## そのほかのコマンド

```bash
uv run glosspop list                    # 人が読む形式で一覧
uv run glosspop list --category "プログラミング"
uv run glosspop rm "<用語名 or slug>"    # 削除
uv run glosspop serve                   # ビューアを起動 (http://127.0.0.1:8765/)
```

## 保存先を変える

`GLOSSPOP_GLOSSARY_DIR` を設定すると辞書の置き場所が変わる。別プロジェクトの
辞書を触るときは、そのプロジェクトの `data/glossary` を指すこと。

## 直接ファイルを編集してもよい

`data/glossary/*.md` を Edit で直接書き換えても反映される（サーバは mtime を見て
読み直す）。frontmatter のキー名は上の表と同じ。ただし `slug` はファイル名が正なので
frontmatter には書かない。
