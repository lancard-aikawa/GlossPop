<!--
  Release 本文の**後半**（前半はその版の変更点で、CHANGELOG.md から差し込まれる）。

  **ここに「できること」を書かないこと。** 機能の一覧は README と MANUAL にあり、
  ここへ写すと 3 か所を直して回ることになる。実際にそうなっていて、**毎回まったく
  同じ 201 行が Release に出ていた** ——「その版で何が変わったのか」がどこにも
  書かれていない、という状態だった。

  ここに置くのは**初めて Releases ページに来た人がその場で要ること**だけ:
  入れ方・更新のしかたと、続きの読み場所。
-->
---

## 初めての方へ

Windows 版（Python のインストールは不要）。

1. `GlossPop-*-win-x64.zip` をダウンロードする
2. **展開する前に** zip を右クリック → プロパティ → 下部の「セキュリティ: 許可する」に
   チェック（`Unblock-File .\GlossPop-*.zip` でも可）。署名していないため、これをしないと
   起動時に SmartScreen の警告が出る
3. 好きな場所に展開して `GlossPop\glosspop.exe` を実行
4. 専用ウィンドウが自動で開く（開かないときはブラウザで http://127.0.0.1:8765/）
5. ファイル一覧の `ようこそ.md` を開いて、知らない語を選択してみる

**終わるときはウィンドウを閉じるだけ**（少し待ってサーバも自分で止まる）。
`glosspop.exe` だけ取り出しても動かないので、`_internal\` と同じフォルダに置いたまま使うこと。
辞書は隣の `data\glossary\` に貯まる。`Program Files` のような書き込めない場所に置くなら
`GLOSSPOP_GLOSSARY_DIR` で保存先を指定する。

`GlossPop-samples-*.zip` は辞書つきの作品フォルダ（青空文庫）。展開してそのフォルダを
開けば、登録済みの辞書がそのまま効く。

## 更新するとき

**上書き展開はしない**（`_internal\` に旧バージョンのファイルが残る）。⚙ の
**更新の確認 → ⬇ 新しい版を隣に展開する** を使えば、隣に展開して起動し直すだけで済む。
手で入れ替えるときは新しいフォルダへ展開し、旧フォルダから `data\` と `content\` を
**丸ごと**コピーすること（`data\window` にお気に入り・最近開いたフォルダ・ネタバレ設定が
入っているので、`data\glossary` だけだと辞書は無事なのに設定が消える）。

⚙ の **データの保存先 → 別の場所に置く** でアプリの外へ移しておけば、次からはコピーが
要らなくなる。**更新したら辞書が空に見えたときも、あわてて消さないこと** —— データは
旧フォルダに残っていて、⚙ の先頭から引き継げる。

## もっと詳しく

| | |
| --- | --- |
| できること・使い方の全部 | [MANUAL.md](https://github.com/lancard-aikawa/GlossPop/blob/main/MANUAL.md) |
| 入手と起動だけ | [README.md](https://github.com/lancard-aikawa/GlossPop/blob/main/README.md) |
| これまでの変更 | [CHANGELOG.md](https://github.com/lancard-aikawa/GlossPop/blob/main/CHANGELOG.md) |

AI の下書きを使うなら [Claude Code](https://claude.com/claude-code) を入れて一度
ログインする（**exe には同梱していない**）。無くても手で登録するぶんには動く。
