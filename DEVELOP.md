# 開発

ソースから動かす・ビルドする・リリースする人向け。使いかたは [MANUAL.md](MANUAL.md)。

**設計の前提と壊しやすい不変条件は [CLAUDE.md](CLAUDE.md) にある。**
コードを触る前にそちらを読むこと —— ここには手順しか書いていない。

## ソースから動かす

```powershell
uv sync
uv run glosspop app                     # 専用ウィンドウで開く
uv run glosspop serve                   # 開くだけ (ブラウザは自分で) http://127.0.0.1:8765/
uv run glosspop serve --port 9000       # ポートを変える
uv run glosspop serve --reload          # 開発用 (ソース変更で再起動)
```

`Ctrl+C` で止まる。**止まらない / 直したのに反映されないときは、ポートを掴んだままの
古いプロセスが残っている**（`uv` の親を kill しても子の python が生き残る）。

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

### 確認用のショートカット

よく使う操作は [`check.cmd`](check.cmd) にまとめてある（引数なしで実行するとメニュー）。

```powershell
.\check.cmd test       # テスト（後ろに書いたものはそのまま pytest へ渡る）
.\check.cmd fast       # ブラウザテスト抜き（-m "not smoke"）
.\check.cmd app        # ソースから起動（専用ウィンドウ）
.\check.cmd build      # exe をビルド（キャッシュを使う。約 21 秒）
.\check.cmd rebuild    # キャッシュを消してビルド（約 48 秒）
.\check.cmd exe        # ビルドした exe を起動して応答を確かめる
.\check.cmd kill       # ポートの所有者を落とす
.\check.cmd all        # test + build + exe
.\check.cmd ci         # リリース前の予行演習（CI と同じ条件。タグは打たない）
```

`exe` は [`packaging/check-exe.ps1`](packaging/check-exe.ps1) を呼ぶ。**凍結してからでないと
壊れないところ**（`DATA_ROOT` の位置、`datas` の漏れ、`hiddenimports` の漏れ）を、
release ワークフローと同じ観点で見る。`serve` で専用ウィンドウが開かないことも確かめる
（開くと CI の起動確認が返ってこなくなる）。

`check.cmd` は **ASCII のみ**で書くこと。`cmd.exe` はバッチをコンソールのコードページ
（日本語環境では CP932）で読むので、UTF-8 の日本語は化ける。さらに CP932 の 2 バイト目が
`0x5C` になる文字（`表` `十` `ソ` など）があると構文解析そのものが壊れる。
日本語を書きたい処理は `.ps1` 側に置く。

Windows 用の exe を自分でビルドする手順は[後述](#windows-向けビルド)。

## 構成

```
glosspop/
  app.py        FastAPI ルーティング
  store.py      辞書 CRUD (カテゴリ別ディレクトリ + frontmatter)
  categories.py カテゴリマスター (categories.yaml)
  models.py     データモデルとカテゴリ名の検証
  relations.py  エントリ間の関係の解決 (ref / 旧 ref / 用語名) と相関図のグラフ
  doctor.py     辞書の点検 (壊れた参照・空の本文など)
  updates.py    新しい版の確認 (外へ通信する唯一の経路)
  installer.py  新しい版の zip を落として隣に展開する
  archive.py    辞書の書き出しと取り込み (取り込みは置き換え。先に控えを取る)
  merge.py      割れてしまった同じものを 1 つにまとめる (下見 → 実行)
  linker.py     レンダリング済み HTML への自動リンク挿入
  render.py     Markdown / テキスト → HTML、本文の改行整形
  fetcher.py    URL 取得
  htmlclean.py  外部 HTML の本文抽出とサニタイズ
  ai.py         claude CLI サブプロセス呼び出し
  cli.py        app / serve / add / list / show / move / merge / rm / categories
  appwindow.py  専用ウィンドウ (ブラウザのアプリモード) の起動
  static/
    viewer.js      ビューア (下層。他の画面はこの上に重なる)
    overlay.js     重ねる仕組み (URL も合わせる。ブラウザの「戻る」で閉じる)
    progress.js    読書位置の記憶 (段落の番号で持つ)
    glossary.js    辞書一覧
    entry.js       用語ページ
    graph.js       相関図 (配置はここ。力学モデルは使わない)
    relations-draft.js 関係の AI 下書き → まとめて書き込み
    doctor.js      辞書の点検
    settings.js    ⚙ (データの保存先・更新の確認)
    update.js      更新のお知らせ
    select-add.js  選択 → 登録 (ビューア・用語ページ・一覧で共用)
    popup.js       吹き出し
    editor.js      登録 / 編集ダイアログ
    merge.js       まとめるダイアログ (相手を選ぶ → 下見 → 実行)
.claude/skills/gloss-add/   Claude Code 用スキル
packaging/                  Windows 向け PyInstaller ビルド
  build.ps1     ビルド実行 (dist/GlossPop/ を作る)
  glosspop.spec PyInstaller の定義
  entry.py      exe のエントリスクリプト
```

## Windows 向けビルド

Python も uv も入っていない PC で動かすなら、PyInstaller で onedir 形式に固める。

```powershell
.\packaging\build.ps1
```

`dist\GlossPop\` ができる。**このフォルダごと**コピーすれば動く（`glosspop.exe` 単体では
動かない。`_internal\` に依存関係が入っている）。

```
dist\GlossPop\
  glosspop.exe    引数なしで起動 = app (専用ウィンドウが開く)
  _internal\      Python ランタイムと依存 (触らない)
  data\glossary\  辞書        ← exe の隣に読み書きされる
  data\window\    専用ウィンドウのブラウザプロファイル (消しても支障はない)
  content\        既定で開くフォルダ (.md / .txt / .html)
```

`glosspop.exe` は CLI もそのまま使える（`glosspop.exe list`、`glosspop.exe add --json -` など）。
辞書と content の場所は環境変数で変えられる（[環境変数](MANUAL.md#環境変数)）。既定は exe の隣なので、
`Program Files` のような書き込めない場所に置く場合は `GLOSSPOP_GLOSSARY_DIR` を指定すること。

exe の隣に何を置くかは `-Seed` で決まる。

| モード | content/ | data/ | 用途 |
| --- | --- | --- | --- |
| `dev`（既定） | リポジトリの `content/` | リポジトリの `data/` | 手元での確認 |
| `dist` | リポジトリの `content/` | `packaging/sample-data/` | 配布用（個人の辞書を入れない） |
| `none` | 入れない | 入れない | 初回起動時に空で作らせる |

リリースの zip は `dist` で作る。サンプル辞書（`冪等` と、カテゴリ違いで 2 件ある `ソース`）が
入っているので、展開してすぐ `ようこそ.md` の中で自動リンクと吹き出しを試せる。

### リリース

**タグを打つ前に `.\check.cmd ci` を通すこと。** release ワークフローと同じ順で、
バージョンの一致・テスト・ビルド・exe の起動まで手元で走らせる予行演習で、
Release は作らない。

これがあるのは、**手元では通って CI で落ちる**失敗を 2 回続けて踏んだため
（`claude` が PATH にあるかの差、実時間に依存するテスト）。落ちるたびに壊れたタグを
消して付け直すことになり、GitHub から失敗のメールも飛ぶ。

`ci` が見ているのは 3 つ:

| 見るもの | なぜ |
| --- | --- |
| `pyproject.toml` と `__init__.py` のバージョン一致 | 食い違うとワークフローが最初で落ちる |
| **`claude` を PATH から外して**全テスト | CI に `claude` は無い。`ai.available()` の分岐が変わる |
| 時間に依存するテストを 40 回 | たまたま通っただけでないか。**網であって証明ではない** |

ネットワークは `tests/conftest.py` が常に塞いでいるので、ここでは何もしない。

タグを push すると [GitHub Actions](.github/workflows/release.yml) がビルドし、
zip を付けた**ドラフト**の Release を作る（内容を確認して手で publish する）。

付く zip は 2 つ:

| | 中身 |
| --- | --- |
| `GlossPop-<version>-win-x64.zip` | アプリ本体 |
| `GlossPop-samples-<version>.zip` | [`samples/`](samples/) の作品フォルダ（辞書つき） |

**サンプルの zip は `samples/` から作る生成物で、git には zip を置かない。**
作品名が日本語なのでアセット名は ASCII 1 本にまとめてある（書き出しで
「カテゴリ名をファイル名に入れない」としているのと同じ理由）。**`.glosspop` が
中に入ったかをワークフローが必ず確かめる** —— ドットで始まるので、圧縮の実装に
よっては黙って落ち、**中身の無いサンプルが配られる**。

**`check.cmd ci` はここまでは見ない。** 予行演習が走らせるのはテストと exe のビルド
までで、**zip を作る 2 つのステップはワークフローの中にしかない**。`v0.9.0` は
そこで 2 回落ちた（1 回目はテスト、2 回目がこの zip）。ワークフローの PowerShell を
触ったら、**同じコマンドを手元の pwsh で 1 回流してから**タグを打つこと。

踏んだのは `Compress-Archive -Path $folders.FullName, samples/README.md` で、
**`samples/` の作品が 2 つ以上のときだけ**入れ子の配列になり、`[String[]]` への変換で
「パス1 パス2」という 1 本の文字列に潰れる。**1 つのときは通ってしまう**ので、
作品を足した回に初めて出る。平らな配列を作ってから渡すこと。

```powershell
# バージョンを 2 箇所とも上げてからタグを打つ
#   pyproject.toml の version
#   glosspop/__init__.py の __version__
git tag v0.1.1
git push origin v0.1.1
```

タグとコードのバージョンが食い違っていると、ビルドせずに失敗する。
Actions は手動実行（workflow_dispatch）もでき、その場合は Release を作らず
artifact に zip を置くだけなので、リリース前の確認に使える。

**`uv.lock` も一緒に上げること。** プロジェクトの版が入っているので、バージョンの
コミットに含め忘れると lock だけ古いまま残る（CI は自分で lock し直すので落ちない ——
**気付けないほうが問題**）。`v0.8.1` で実際にやった。

**タグは push して初めてリリースになる。** ローカルで打っただけではワークフローが
動かず、Release も作られない（`v0.7.0` がその状態で残っていたが、v0.9.0 の作業で
消した。番号が 0.6.0 → 0.8.0 と飛んでいるのはそのため）。

**落ちたら、タグを消して付け直す。**

```powershell
git push origin :refs/tags/v0.9.0   # リモートから消す
git tag -d v0.9.0                   # 手元からも消す
# 直してコミットしてから打ち直す
```

失敗した回は Release を作らずに終わるので、消して打ち直しても外に出たものは無い。

## テスト

```powershell
uv run pytest
uv run pytest tests/test_smoke_ui.py     # ブラウザで実際に動かす通しテストだけ
```

### 全部走らせない

全件はブラウザテストのぶんリニアに伸びる（`tests/test_smoke_ui.py` は 1 テストごとに
Chrome を起動する）。直しながら回すときは範囲を絞ること。

```powershell
.\check.cmd fast                              # smoke 以外（-m "not smoke"）
.\check.cmd test tests\test_linker.py         # 1 ファイル
.\check.cmd test tests\test_linker.py::test_longest_match_wins
.\check.cmd fast -k trie                      # 名前で絞る
uv run pytest --durations=10                  # どこが重いか
```

`test` / `fast` の後ろに書いたものは**そのまま pytest へ渡る**。印は
`tests/test_smoke_ui.py` の `pytestmark` 1 か所で、`pyproject.toml` の `markers` に
登録してある（**印を足したら両方**）。

**絞れるのは編集中だけ。** `check.cmd all` と `check.cmd ci` は今までどおり全件で、
ここを `fast` に置き換えないこと —— HTML は正しいのに JS が落ちている、という壊れ方は
smoke でしか捕まらない。

VSCode から回すなら [`.vscode/settings.json`](.vscode/settings.json) がテストパネルを
有効にしてある。▶ が通常実行（出力は「Python」出力チャネル）、🐞 がデバッグ実行
（出力はデバッグコンソール、ブレークポイントも止まる）。**収集は全件のまま**にして
あるので、smoke もパネルには出る（1 件だけ押せることのほうが大事）。

`tests/test_smoke_ui.py` は本物のサーバを立てて Chrome で操作する。「登録 → 本文で
リンクになる → 吹き出しが出る → 関係が図になる → 点検が黙る」まで通す —— この
リポジトリの機能は、そこまで見ないと動いているか分からない（HTML は正しいのに JS が
落ちている、が起きる）。**手元の Chrome を使う**ので、ブラウザのダウンロードは要らない。
Chrome も playwright も無い環境では丸ごと skip する。
