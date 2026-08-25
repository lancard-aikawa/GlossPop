# X のカード（一次資料。実測より先に読むもの）

> **これは資料であって判断ではない。** 一次資料に書いてあることと、書いていないことを
> 並べただけで、「だからどう作るか」はここには書いていない。
>
> **調べた日: 2026-08-24。** CLAUDE.md の「X に貼ったときの見え方は実測で決まっている」は
> **4 度誤診した記録**が主で、**規約を読んだ記録が無かった** —— 数分で「タグが足りない」と
> 決める前に、まずここを見ること。

## まず: 公式ドキュメントは公開から引き上げられている

| 確かめたこと | 結果 |
| --- | --- |
| `developer.x.com/en/docs/x-for-websites/cards/overview/markup` | **307 → `docs.x.com/overview`**（ページごと消滅） |
| `docs.x.com/llms-full.txt`（全文 5.4 MB） | `twitter:card` / `twitter:image` / `summary_large_image` の出現 **0 件**。残る "card" は Ads API の creative と、文書サイトの UI 部品 |
| `help.x.com` | Cloudflare の関門で 403 |

**生きている一次資料は 2 つだけ**:

- X 公式アカウント **XDevelopers** が書いた開発者フォーラムの固定投稿（最終更新 **2022-08-02**）
- 消える前の `developer.twitter.com` の**保存版**（**2023-12-29** 取得）

**孫引きを資料にしないこと。** 検索に出る解説記事は数字が食い違う（GIF は 15MB まで、
推奨は 1200×628、など出典の無い値が混ざる）。以下は全部、上の 2 つからの逐語。

## 画像の形式と大きさ

> **twitter:image** — URL of image to use in the card. **Images must be less than 5MB in
> size. JPG, PNG, WEBP and GIF formats are supported. Only the first frame of an animated
> GIF will be used. SVG is not supported.**

> Images for this Card support an **aspect ratio of 2:1 with minimum dimensions of
> 300x157 or maximum of 4096x4096 pixels.**

> The dimensions of the image are smaller than the recommended size.
> **Images should be a minimum of 144 x 144 pixels in size.**

ここから決まること 2 つ:

- **SVG は使えない。** 地図の絵は SVG が通る側（`MAP_SUFFIXES` の先頭）で、
  `samples/戦国時代` の 2 枚も SVG —— **絵をそのままカードには渡せない**
- **いまの `card.png` は範囲内**（`CARD_W` 1200 × `CARD_H` 630 を `PNG_SCALE` 2 倍 ＝
  **2400×1260**）。「`og:image` の大きさが原因」という過去の誤診は、**文書の側から
  否定される**

## 拡張子は要るのか —— 書かれていない

markup リファレンス・troubleshooting・固定投稿を通して、**"extension" という語は
1 度も出てこない。** URL について書いてあるのはこれだけ:

> if your image is not showing in the preview on Twitter, is it accessible on a URL that
> is not blocked by your `robots.txt` file? Does it conform to our size constraints?
> **Are you using an absolute and full URL (including the https protocol piece), not a
> relative one?**

> The most common cause is that the image specified via the `twitter:image` tag
> **is not publicly accessible on the web.**

判定基準は **形式・大きさ・公開されているか・絶対 URL か**で、名前の形は条件に入って
いない。**ただし「不要」と書いてあるわけでもない** —— 書かれていないことは、
書かれていないままにしておくこと。

OGP 側も同じで、`og:image` は "An image URL"、`og:image:type` は "A MIME type for this
image" とあるだけ（[ogp.me](https://ogp.me/)）。

## クエリは受け取られる。公式が指定している回避策そのもの

> Images referenced in a Card are also **cached based on URL**. This often causes images
> to not update when the above Card refresh technique is used. **To work-around this
> issue, you can add an extra parameter at the end of your image URL so that the
> Twitterbot treats the image as a unique URL and re-fetches the image.** For example:
> `<meta name="twitter:image" content="http://example.com/myimage.jpg?4362984378">`

`publish.py` の `?v=<中身の印>` は、**文書に書いてある手順そのまま**だった。

ページのほうも同じ:

> …adding placeholder value parameters to the end of your URL (`http://www.test.com/?x=test1`)
> or a unique hash (`http://www.test.com/#test1`) **will generally not affect the page
> contents, but will generate a unique bit.ly URL** for each unique value of x.

**ページのキャッシュも URL 単位で、読み直させる公式の方法は「違う URL で貼る」。**
CLAUDE.md の「試すたびに初見の URL が要る」「`?v=` が効くのは画像だけ」は文書と一致
している。

## キャッシュは約 7 日

> Once your domain is known to the validator, the web crawler re-indexes the meta
> information on your tag **roughly every seven days.**

**「公開直後にカードが出ない」の説明にはならない** —— これは既に知られている URL の
**再クロール**の話で、初回の取得について書かれたものではない。

## ページの大きさに上限がある（2 MB）

> **Twitter's crawler has a limit of 2 MB for page responses.** If you are seeing this
> issue, you should try reducing the size of the HTML response so that it is below the
> limit.

**いまは当たらない** —— 本文のページ（`docs/*.html`）に `og:` も `twitter:` も 1 つも
無いので、クローラは取りに来ない。**そこにカードを付けるなら、この上限が付いてくる**
（本文 HTML と辞書 JSON を 1 枚に埋める作りなので、長編で超えうる。**未測定**）。
辞書の 1 枚（`index.html`）のほうは余裕がある（39 語の辞書で Markdown 合計 70 KB）。

## クローラの前提（固定投稿から）

- **JavaScript を実行しない。タグは静的でなければならない**
- ユーザーエージェントは `Twitterbot/1.0`。**画像が別ドメインなら、そちらの
  `robots.txt` も見る**
- **数値 IP と `localhost` は不可**（fully qualified DNS domain name が要る）
- TLS v1.1 以上。証明書とサーバ名が一致していること
- ページに妥当な `Content-Type` があること
- **Card Validator のプレビューは 2022 年半ばに廃止**。「Tweet composer に貼って
  確かめよ」と書いてある（＝**こちらから読み直させる手段は無い**）

## 文書が答えていないこと

**ここに無いものを「規約で決まっている」と言わないこと。**

- **公開直後にカードが出ない理由**（7 日の再クロールは書いてあるが、初回の話ではない）
- **拡張子の要否**
- 見せ方 —— 説明文が出ないこと、見出しの札が画像の左下に重なること。
  どちらも**実測でしか分かっていない**（→ CLAUDE.md）

## 出典

- [Card error, unable to render, or no image: READ THIS FIRST](https://devcommunity.x.com/t/card-error-unable-to-render-or-no-image-read-this-first/62736)
  —— XDevelopers、X Developers forum（2022-08-02 更新）
- Cards markup / Summary with large image / Troubleshooting Cards ——
  `developer.twitter.com/en/docs/twitter-for-websites/cards/…` の **2023-12-29 保存版**。
  現行 URL は `docs.x.com` へ 307 で、内容は残っていない
- [The Open Graph protocol](https://ogp.me/)
