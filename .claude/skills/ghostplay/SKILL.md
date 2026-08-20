---
name: ghostplay
description: このプロジェクトの実演解説動画 (失敗例 → 原因 → 正解ルート) の台本 plan.json を作る。ユーザーが「デモ動画を作って」「使い方を動画にして」「プレイ動画を作って」と言ったときに使う。
---

# GhostMoviePlay — Pass1 (台本づくり)

あなたの仕事は **plan.json を書くこと** です。収録も動画の書き出しも
`gmp record` / `gmp render` が決定論的にやるので、あなたは台本だけを完成させます。

## 手順

### 1. 対象を理解する

**ソースを先に読む。** UI を触るより前に、

- 勝敗条件・スコア計算・状態遷移がどこに書いてあるか
- 「良い操作」と「悪い操作」を分けている実際のロジック
- 乱数・時刻・外部通信など、再生のたびに結果が変わる要素

を押さえる。ここを飛ばすと、ただ下手なだけで教材にならない失敗例ができます。
このツールの価値は **裏を知った上で狙って失敗できること** です。

### 2. 実際に触る

Playwright MCP で対象を開き、想定した操作が通るか、セレクタが実在するかを確認する。
**推測で書いたセレクタは収録時に必ず落ちます。** 開いた状態の DOM から取ること。

canvas / WebGL で a11y スナップショットが効かない場合は、収録用の状態取得フックを
アプリ側に足す提案をする（`window.__probe = () => state` 程度）。

### 3. 演目を組む

既定の 3 幕構成:

1. **失敗** — 具体的な因果のある悪手を実演する。「この項を無視すると詰む」が言えるもの。
   ランダムな下手打ちではダメ。
2. **原因** — 画面を止めて `highlight` で指しながら説明する。ここでは操作しない。
3. **正解** — 同じ場面をやりなおして、良い結果まで通す。

### 4. plan.json を書く

`plan.json` はこのプロジェクトの git に置きます（`video.md` の隣）。
生成物はユーザフォルダ側に出るので、プロジェクトを汚しません（`gmp where` で確認できます）。

`gmp record <plan.json>` を実行して最後まで通ることを確認する。
落ちたセレクタは直して再実行。通ったら `gmp build <plan.json>` で mp4 になります。

## 台本の書き方

**1 ビート = 1 字幕 = 1 メッセージ。** 字幕はビート開始から終了まで出しっぱなしです。

- 字幕は **2 行 / 26 文字** に収まる長さにする。長い説明はビートを割る。
- `hold` は読み切れる長さに。目安は **文字数 / 8 + 0.6 秒**。
  音声を付ける場合は音声の尺が優先されるので、`hold` は下限として効く。
- 解説だけのビートは `actions` を空にして `hold` だけ置く。
- 口調は指定に従う。`say` は音声用、`subtitle` は口調を保ったまま短く整えたもの
  （同じでよければ `subtitle` は省略）。

### 音声を付ける場合

`say` は **読み上げられる前提** で書く。TTS が誤読しやすいものは避けるか開いて書く:

- 記号・英数字の混在（`#tile-1` → 「タイルの1番」）
- 単位や略語（`3px` → 「3ピクセル」）
- 顔文字・括弧書きの補足

`voice` に話者を書いておくと `gmp voice` がそのまま使う:

```json
"voice": { "engine": "voicevox", "speaker": "ずんだもん", "style": "ノーマル", "speed": 1.0 }
```

**原稿を書いたら `gmp kana <plan.json> --out kana.txt` で読みを確認すること。**
TTS は文脈の薄い単語を誤読する（「語」は単独だと **カタリ**）。誤読があれば
`voice.dict` に読みを書く。ルビは振れない。

```json
"voice": { "dict": { "語": "ゴ", "冪等": { "pronunciation": "ベキトウ", "accent": 0 } } }
```

読み方が変わるのは単独で出たときだけで、「用語」「物語」などの複合語は影響を受けない。

クレジット表記は `gmp voice` が話者名から自動で埋めて動画に焼くので、
台本側で用意する必要はない。指定の表記があるときだけ `voice.credit` に書く。

## スキーマ

```jsonc
{
  "version": 1,
  "meta":  { "title": "動画タイトル", "lang": "ja", "project": "プロジェクト名" },
  "app":   { "url": "http://localhost:5173", "ready": "text=スタート",
             "start": "npm run dev", "cwd": "." },
  "video": { "width": 1280, "height": 720, "fps": 30, "leader": 2.5, "trailer": 1.5 },
  "voice": { "engine": "voicevox", "speaker": "ずんだもん", "style": "ノーマル", "speed": 1.0 },
  "determinism": { "seed": 12345, "time": "2026-01-01T09:00:00" },
  "scenes": [
    {
      "id": "fail-greedy",
      "title": "よくある失敗",
      "beats": [
        {
          "say": "ナレーション原稿",
          "subtitle": "字幕（省略可）",
          "hold": 2.4,
          "actions": [ /* ↓ */ ]
        }
      ]
    }
  ]
}
```

action:

| type | 必須キー | 備考 |
|---|---|---|
| `goto` | `url` | |
| `click` / `dblclick` | `selector` | カーソルが滑って波紋が出てから押される |
| `hover` | `selector` | |
| `type` | `selector`, `text` | `delay` で打鍵速度 |
| `press` | `key` | |
| `select` | `selector`, `value` | |
| `scroll_to` | `selector` | |
| `select_text` | `text` | 本文をマウスでなぞって選択する。`selector` は探す範囲 (既定 `article`)、`occurrence` は何番目か (0 始まり) |
| `highlight` | `selector` | `duration` 秒だけ光らせる。画面外なら自動で送る。省略時は次のビートまで点灯 |
| `wait_for` | `selector` か `seconds` | `state` は既定 `visible` |
| `sleep` | `seconds` | |
| `eval` | `expr` | 状態注入・乱数固定に使う |

## 注意

- **GlossPop 固有のことは下の節にまとめてある。** 先に読むこと。
- **決定論性。** 乱数や時刻に依存する挙動があれば `determinism` で潰す。

  ```json
  "determinism": { "seed": 12345, "time": "2026-01-01T09:00:00" }
  ```

  `seed` は `Math.random` を固定する。アプリが `crypto.getRandomValues` や
  サーバ側の乱数を使っている場合はこれでは効かないので、`eval` で状態を直接
  注入するか、それも無理ならその旨をユーザーに伝える。
- **サーバ。** 開発サーバが必要なら `app.start` と `app.cwd` を書く。
  すでに立っていれば起動されないので、書いておいて損はない。
- **無効化された要素をクリックしない。** ゲーム終了後の盤面など、`disabled` な
  要素への `click` はタイムアウトします。解説ビートは `highlight` だけにする。
- **冒頭で結論を言う。** 「大きい数から取ると損をする」のような一行を intro に置く。

---

# GlossPop で撮るとき

既に 1 本ある: `docs/video/gloss-scope/`（登録する語の粒度を扱う）。
まずそれを読むと、ここに書いたことが実物で分かる。

## サーバは serve.py で起動する

`plan.json` の `app` に次を書く。**実辞書を触らず、撮り直すたびに 0 語から始まる。**

```json
"app": {
  "url": "http://127.0.0.1:8765/",
  "ready": "#files button",
  "start": "python docs/video/gloss-scope/serve.py",
  "cwd": "../../..",
  "start_timeout": 90
}
```

`serve.py` は `GLOSSPOP_DATA_ROOT` を使い捨ての一時ディレクトリに向けて
`content/ようこそ.md` だけを置く。新しい動画を撮るなら、その動画用の
`serve.py` を横に置いて、必要な初期状態（開く文書・仕込む辞書）を用意する。

**自分で `uv run glosspop serve` を回したままだと、そちらが使われて初期状態が
揃わない。** `gmp record` は既に応答していればサーバを起動しないため。
ポートの持ち主を落としてから撮ること（CLAUDE.md の「サーバの止め方」）。

## セレクタ

ダイアログとフォームは `data-ref` を持っているので、それで掴む。

| | |
| --- | --- |
| ファイル一覧 | `#files button[data-path="ようこそ.md"]` |
| 選択後に出る登録ボタン | `.sel-add` |
| 登録／編集ダイアログ | `dialog[open]`（**他にもダイアログがあるので `[open]` は必須**） |
| ダイアログの各欄 | `dialog[open] [data-ref="term"]` — `reading` `aliases` `category` `newCategory` `subcategory` `scope` `summary` `definition` `examples` `tags` `source` `spoiler` `save` `cancel` `draft` |
| 自動リンク | `a.gloss-link`、`a.gloss-link[data-gloss="ソース"]` |
| 吹き出し | `.gloss-pop` |

## 踏むこと

- **AI 下書き（`✨`）は使わない。** Claude CLI を呼ぶので時間が読めず、毎回結果が変わる。
  台本では手入力（`type`）で埋める
- **本文の選択は `select_text` を使う。** `.sel-add` は `mouseup` を見て出る
- **読書位置の復元でページが非同期にスクロールする。** `select_text` は照合して
  測り直すので大丈夫だが、座標に依存する自前の `eval` を書くときは注意
- **既に登録済みの語を選ぶと「用語を編集」になる。** 同じ表記で 2 件目を作る操作では
  なく、カテゴリを変えると**移動**になる（`former_refs` が残る）。同表記異義語を
  実演したいなら別の経路を探すこと
- **カテゴリが 0 件のときは `category` が `/new` になり `newCategory` 欄が出る。**
  1 件でもあると既存が選ばれるので、`/new` を明示的に選んでから `newCategory` を埋める
- **「語」は VOICEVOX に カタリ と読まれる。** `voice.dict` に `{"語": {"pronunciation": "ゴ", "accent": 1}}`
  を入れる。原稿を書いたら `gmp kana` で確認すること

## 題材の探しかた

MANUAL.md の「自動リンクの規則」「ネタバレを防ぐ」「フォルダ辞書」あたりに、
**知らないと踏む**性質が書いてある。そこが失敗例の種になる。
`content/ようこそ.md` は説明のために書かれた文書なので、教材として使いやすい。
