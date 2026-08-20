# 解説動画

GlossPop の使い方を、**失敗例 → その原因 → 正解ルート** の 3 幕で見せる動画。
[GhostMoviePlay](https://github.com/lancard-aikawa/GhostMoviePlay)（`gmp`）で作る。

| 動画 | 扱っていること |
| --- | --- |
| [`gloss-scope/`](gloss-scope/) | 登録する語の粒度。「リンク」を登録すると本文が 8 か所リンクだらけになる → 部分文字列マッチだから → 「自動リンク」に直して 1 か所 |

## ここに入るもの / 入らないもの

**入るのはソースだけ。** 生成物（wav・webm・mp4・字幕）はユーザフォルダ側
（`~/Videos/GhostMoviePlay/GlossPop/<動画名>/`）に出るので、`.gitignore` は要らない。

| ファイル | |
| --- | --- |
| `video.md` | 何を撮るかの指示（手で書く） |
| `plan.json` | 台本。AI が書き、人が直す。**これが資産** |
| `serve.py` | 収録用にアプリを起動するスクリプト |

**`plan.json` はセレクタと画面の挙動に依存していて腐る。** ここに置いてあるのは、
UI を変えたときに同じ diff で気づけるようにするため。

## 撮りかた

```powershell
# 1. VOICEVOX ENGINE を上げる（音声を付けるなら）
Start-Process "$env:LOCALAPPDATA\Programs\VOICEVOX\vv-engine\run.exe" `
  -ArgumentList "--host","127.0.0.1","--port","50021" -WindowStyle Hidden

# 2. 自分で回しているサーバがあれば落とす（後述）
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 3. 撮る
cd C:\Repos\mywork\GhostMoviePlay
uv run gmp build C:\Repos\mywork\GlossPop\docs\video\gloss-scope\plan.json --voice
```

出来上がりの場所は `uv run gmp where <plan.json>` で分かる。

台本を直したら `gmp build` を回し直すだけでよい。**原稿が変わっていないビートの
音声は再合成されない**ので、部分的な手直しは速い。

## 新しく撮る

```powershell
cd C:\Repos\mywork\GhostMoviePlay
uv run gmp init C:\Repos\mywork\GlossPop\docs\video\<動画名>
```

`video.md` を書いたら、**このリポジトリを開いた Claude Code に「デモ動画を作って」
と頼む**。`.claude/skills/ghostplay/` が台本の作り方を持っている。
そこに GlossPop 固有の注意（セレクタ・踏むこと・題材の探しかた）もまとめてある。

## 収録は実辞書を触らない

`serve.py` が `GLOSSPOP_DATA_ROOT` を使い捨ての一時ディレクトリに向け、
`content/ようこそ.md` だけを置いてサーバを起動する。

- **実際の辞書（`data/glossary/`）は一切触らない**
- **撮り直すたびに「0 語登録」から始まる** —— 同じ台本から同じ絵が録れる

新しい動画で別の初期状態が要るなら、その動画のフォルダに `serve.py` を置いて用意する。

**注意: 自分で `uv run glosspop serve` を回したままだと、そちらが使われる。**
`gmp record` は既に応答しているサーバを起動し直さないため、初期状態が揃わないまま
撮れてしまう。上の手順 2 でポートの持ち主を落としてから撮ること。

## 直すとき

画面の構造（`data-ref`・クラス名・ファイル一覧の作り）を変えたら、`plan.json` の
セレクタが落ちる。`gmp record <plan.json>` を通せば落ちた箇所が分かる。
