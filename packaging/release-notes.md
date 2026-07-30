Windows 版（Python のインストールは不要）。

## 使い方

1. `GlossPop-*-win-x64.zip` をダウンロードする
2. **展開する前に** zip を右クリック → プロパティ → 下部の「セキュリティ: 許可する」に
   チェック（`Unblock-File .\GlossPop-*.zip` でも可）。署名していないため、これをしないと
   起動時に SmartScreen の警告が出る
3. 好きな場所に展開して `GlossPop\glosspop.exe` を実行
4. ブラウザで http://127.0.0.1:8765/ を開く
5. ファイル一覧の `ようこそ.md` を開く

サンプルとして 3 語（`冪等` と、プログラミング / 料理で意味が違う `ソース`）が最初から
入っている。`ようこそ.md` の中で自動リンク・吹き出し・同表記の使い分けをそのまま試せる。
不要なら `data\glossary\` の中を消せばよい。

`glosspop.exe` だけ取り出しても動かない。`_internal\` と同じフォルダに置いたまま使うこと。

辞書は exe と同じ場所の `data\glossary\` に保存される。書き込めない場所
（`Program Files` など）に置く場合は環境変数 `GLOSSPOP_GLOSSARY_DIR` で保存先を指定する。

## 更新するとき

上書き展開はしない（`_internal\` に旧バージョンのファイルが残る）。新しいフォルダへ
展開して、旧フォルダから `data\` と `content\` をコピーする。

## AI 下書きについて

用語の下書き生成は PATH にある [Claude Code](https://claude.com/claude-code) CLI
（`claude`）を呼び出す。無い場合はその機能だけ無効になり、手入力での登録はできる。
