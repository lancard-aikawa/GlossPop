---
name: gloss-add
description: GlossPop の用語辞書に用語を登録・更新する。ユーザーが「この言葉を辞書に登録して」「用語集に追加」「GlossPop に登録」と言ったとき、または会話やコード中の専門用語を辞書化したいときに使う。カテゴリ/サブカテゴリを既存構成に揃えて 1 用語 = 1 Markdown ファイルで保存する。
---

# gloss-add — GlossPop 辞書への登録

GlossPop の辞書は `data/glossary/<カテゴリ>/<slug>.md`（1 用語 = 1 ファイル、
YAML frontmatter + 本文）として保存される。**ディレクトリ名がカテゴリ、ファイル名が
slug の正**で、frontmatter には書かない。ここに書いた用語は、ビューアでテキストを
表示したときに自動リンクになり、ホバーで吹き出しが出る。

一意キーは **カテゴリ + 用語**。同じ「ソース」をプログラミングと料理の両方に持てる。

## 手順

### 1. 既存を確認する

同じ用語や別名がすでに無いか、カテゴリ構成がどうなっているかを先に見る。

```bash
uv run glosspop list --json
uv run glosspop categories                 # マスター（用語 0 件のカテゴリも出る）
uv run glosspop show "<用語名>"             # あれば内容が出る。無ければ exit 1
```

同名が複数カテゴリにあると `show` / `rm` / `move` は「複数あります」と言って止まるので、
`--category` で絞る。

### 2. 内容を組み立てる

以下のフィールドで JSON を作る。**日本語で書く**。

| フィールド | 必須 | 内容 |
| --- | --- | --- |
| `term` | ✓ | 見出し語。表記は本文中に実際に現れる形に合わせる |
| `reading` | | 日本語の読み（かな）。ソート順に使う。英語のみの語なら空 |
| `aliases` | | 表記ゆれ・略称・英語表記。**本文中で自動リンクの対象になる**ので、同義でない語は入れない |
| `category` | ✓ | 大分類 = ディレクトリ名。分野が合う既存カテゴリがあれば使い、無ければ新設する |
| `subcategory` | | 小分類。**選んだカテゴリの下にあるものだけ**から選ぶ。不要なら省略 |
| `summary` | ✓ | 吹き出しに出る 1〜2 文（120 字以内）。ここだけで意味が分かるように書く |
| `definition` | ✓ | 辞書ページ本文。Markdown。背景と使いどころを 3〜6 文 |
| `examples` | | 使用例（文字列の配列） |
| `related` | | 関連語。既存の `term` と一致すればリンクになる |
| `tags` | | 検索用タグ |
| `source` | | 出典（ファイル名・URL・「〇〇との会話」など） |
| `first_file` | | 初出のファイル（ビューアで開くフォルダからの相対パス） |
| `first_locator` | | 初出の位置（`L.42` など。表示とジャンプに使う） |

注意点:

- **`aliases` は慎重に。** 自動リンクは本文の部分文字列にマッチするので、
  一般的すぎる語（「処理」「設定」など）を別名に入れると本文がリンクだらけになる。
- **分野が合わないカテゴリに無理に寄せない。** 音楽の用語を「プログラミング」に
  入れるくらいなら新設する。`未分類` は使わない。
- `definition` に長いコードブロックを入れない（吹き出しでは非表示になる）。
- `definition` は 1 文ごとに改行し、話題が変わるところで空行を入れる
  （保存時にも自動で 1 文 1 行に整形される）。

### カテゴリ名の制約

ディレクトリ名になるので、どの OS でも作れる文字だけを使う。破ると 422 で弾かれる。

- 使えない文字: `< > : " / \ | ? * # %` と制御文字
- 先頭・末尾に `.` や空白を置かない
- Windows 予約名は不可: `CON` `PRN` `AUX` `NUL` `COM1`〜`COM9` `LPT1`〜`LPT9`
- 40 文字以内

日本語・空白・`+` `・` などは使える。

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
`ref`（= `カテゴリ/slug`）が保存先の ID。

**衝突判定は同一カテゴリ内だけ。** 別カテゴリに同名があっても `created` になる
（それが狙いの機能）。`exists` が返るのは、そのカテゴリに既に同じ用語があるとき。

### 4. 既存を上書きするとき

`--update` を付ける。JSON の `category` と `term` の組で対象を探して更新する。

```bash
uv run glosspop add --json @/tmp/gloss-entry.json --update
```

**`--update` は本文ごと置き換える。** 既存の内容を残したい場合は先に
`uv run glosspop show "<用語名>" --json` で読み、マージした JSON を作ってから渡すこと。

## そのほかのコマンド

```bash
uv run glosspop list                            # 人が読む形式で一覧
uv run glosspop list --category "プログラミング"
uv run glosspop move ソース --to 料理             # カテゴリ移動（ファイルごと動く）
uv run glosspop rm ソース --category 料理         # 削除
uv run glosspop categories --add 音楽            # 用語 0 件でもカテゴリを作れる
uv run glosspop categories --rename 旧名 新名     # ディレクトリごと改名
uv run glosspop categories --remove 音楽         # 空のカテゴリだけ削除できる
uv run glosspop serve                           # ビューアを起動 (http://127.0.0.1:8765/)
```

## 保存先を変える

`GLOSSPOP_GLOSSARY_DIR` を設定すると辞書の置き場所が変わる。別プロジェクトの
辞書を触るときは、そのプロジェクトの `data/glossary` を指すこと。

## 直接ファイルを編集してもよい

`data/glossary/<カテゴリ>/*.md` を Edit で直接書き換えても反映される（サーバは mtime を
見て読み直す）。frontmatter のキー名は上の表と同じ。ただし **`category` と `slug`、
`scope` は書かない** — ディレクトリ名・ファイル名・置き場所が正なので、frontmatter に
書いても無視される。

なお `glosspop` CLI が書くのは**全体の辞書**（`data/glossary/`）だけ。ビューアには
「そのフォルダを開いている間だけ有効なローカル辞書」もあるが、そちらは
`<フォルダ>/.glosspop/glossary/` に置かれ、CLI からは触らない。

`mkdir` でカテゴリのディレクトリを作っただけでも、次の読み込み時に
`data/categories.yaml` へ自動で取り込まれる。
