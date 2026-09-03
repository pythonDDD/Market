# 潮目 — 市場の連動と、その崩れ

世界の株式・為替・商品・金利・暗号資産を横断してスキャンし、
「いつもと違う動き」を自動で見つけて上に出すサイト。

公開URL: https://pythonddd.github.io/Market/

## 構成

```
scripts/
  healthcheck2.py   データ源の疎通確認（設計を変えるときに再実行する）
  sources.py        取得層。Yahoo / Coinbase / Kraken / CoinGecko / Hyperliquid など
  analytics.py      分析ロジック。外部ライブラリなしの純Python
  build.py          全体を束ねる。docs/data/latest.json などを作る
  chart.py          PNGを描く。zlibとビットフォントだけの自前実装
  daily.py          毎朝9時の「朝の便り」。画像つきでLINEに送る
  notify.py         しきい値に触れたときだけLINEに通知する
docs/               GitHub Pages が配信するフォルダ
  index.html        構造だけ。中身は app.js が埋める
  app.js            描画
  style.css
  data/             build.py が生成（リポジトリにはコミットしない）
    latest.json       画面が最初に読む。分析結果ぜんぶ
    history.json      日足と四本値のキャッシュ。ローソク足もここから読む
    ohlc.json         build.py が書いているが、いまの app.js は読んでいない（後述）
    daily.png         朝の便りの画像。LINEはこのURLを取りにくる
    daily_prev.png    その縮小版（トーク画面に出るほう）
    daily_msg.json    組み立て済みのメッセージ
    notify_state.json 送信の記録（連投防止・月間通数・当日送信済みか）
.github/workflows/
  update.yml        15分ごとに実行してPagesへ配信
  healthcheck.yml   手動実行の疎通確認
```

## 使うデータ源

疎通確認で実際に通ったものだけを使っている。

| 用途 | 取得元 | 備考 |
|---|---|---|
| 株価指数・先物・商品・為替・金利 43銘柄 | Yahoo Finance | 公式APIではない。仕様変更のリスクあり |
| 暗号資産の日足 | Coinbase（控えに Kraken） | |
| 時価総額・ドミナンス | CoinGecko | キー不要 |
| 資金調達レート・建玉 | Hyperliquid | fundingは1時間あたりの値 |
| 恐怖強欲指数 | alternative.me | |

**使わないもの（実測で落ちたため）**

| 取得元 | 症状 |
|---|---|
| Stooq | HTTP 200 を返すが中身はJavaScript検証ページ。日本・米国どちらのIPからも遮断 |
| Binance | 米国IPから HTTP 451（地域ブロック）。Actionsのランナーは米国 |
| Bybit | 米国IPから HTTP 403（CloudFrontの国別遮断） |

## 手元での動かし方

```bash
python scripts/build.py --selftest   # ネット無しで全体を検証
python scripts/chart.py  --selftest  # 描画部分の検証
python scripts/daily.py  --selftest  # 朝の便りの検証
python scripts/notify.py --selftest  # 通知の検証
python scripts/build.py --mock       # 架空データで通し確認
python scripts/daily.py --mock       # 架空データで朝の便りの画像を作る
python scripts/build.py --full       # 実データを取得（履歴も）
python -m http.server 8000 -d docs   # http://localhost:8000 で確認
```

## 設計上の注意

- **確定した日足と現値は別物として扱う。** アジア市場は取得時点で取引中のことが多く、
  最終足と現値が数％ずれる。相関の計算には確定足だけを使っている。
- **利回りは差分、価格は変化率。** 4.50% → 4.79% を「+6.4%」と書くのは誤り。
- **Yahooが返す通貨は当てにしない。** VIXも米国債利回りも "USD" と返ってくる。
  単位は `sources.py` の `SYMBOLS` で持っている。
- **取得に失敗した銘柄は前回値を残し、必ず画面に警告を出す。**
  黙って古い値を新しい顔で出さない。
- **予測はしない。** 「似た局面のその後」は過去の実績分布であって予測ではない。
  該当した日数だけでなく、連続日をまとめた「独立した局面の数」も併記している。

## 既知の制約

- 株価指数の取得先が Yahoo 一本。代替が無いため、ここが止まるとTradFi側が止まる。
- GitHub の定期実行は混雑時に遅れることがあり、15分ちょうどに走る保証はない。
  また、リポジトリが60日間更新されないと定期実行は自動で止まる。
- 曜日・月のアノマリーは、t値が2未満のセルに点線を付けているが、
  それでも多重比較の問題は残る。売買の根拠にはしないこと。
- `docs/data/ohlc.json` は `build.py` が書いているが、`app.js` は読んでいない。
  ローソク足は `history.json` から描いている。消しても画面は変わらないはず
  だが、確かめてから消すこと。

## 朝の便り（毎日9時のLINE）

毎朝9時ごろ、その日の市況を画像1枚つきでLINEに送ります。Secrets を入れるだけで動きます。

### 設定

1. LINE Developers で Messaging API のチャネルを作る
2. リポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
   - `LINE_CHANNEL_TOKEN` … チャネルアクセストークン（長期）
   - `LINE_TO` … 送信先のユーザーID（`U` で始まる文字列）
3. そのLINE公式アカウントを、自分のLINEで友だち追加しておく
   （**友だちでないユーザーには push できません**）

### 仕組み

15分ごとの `update.yml` に相乗りしています。

| 時刻（日本時間） | やること |
|---|---|
| 8:40以降の最初の実行 | 画像を描いて `docs/data/` に置く。その回の配信で公開される |
| 9:00以降の最初の実行 | 公開されたURLを確かめてから送る |

**画像がPagesに載る前に送っても相手には出ない**ため、この2段構えにしています。
専用のワークフローを別に立てると「送った」という記録を配信物に残せず、
翌日以降の重複判定が効きません。

そのため到着は **おおむね9時00分〜9時20分** です。
GitHubの定期実行は混雑時に遅れるので、9時ちょうどは保証できません。

送るのは吹き出し2つ（Flexの要約＋画像）ですが、
[公式ドキュメント](https://developers.line.biz/ja/docs/messaging-api/pricing/)によれば
通数は送信対象の人数で数えるため、**1通**です。無料枠は月200通。

### 落ちないための備え

- 画像の公開が確認できないときは、4回（約1時間）まで様子を見て、
  それでも駄目なら**画像を諦めて文字だけ送る**。黙って落とさない
- Flexが `400` で弾かれたときは、文字だけで送り直す
- 同じ日に二度送らない。前日の画像を使い回さない
- 画像の大きさは、公開されているものと手元のものを**バイト数まで照合**する。
  URLが見えるだけでは、CDNが古い画像を返している場合を弾けないため

### 画像について正直に

画像は `chart.py` が zlib とビットマップフォントだけで描いています。
外部ライブラリを入れていないのは、毎朝の `pip install` が失敗すると
その日の通知ごと落ちるためです。

**この描画器は日本語を出せません。** 画像の中の文字は英数字と記号だけです。
日本語の説明はFlexメッセージの本文側に載せています。
画像にも日本語を入れたい場合は、フォントファイル（Noto Sans JP 等）と
Pillow が必要になります。

### 手元での確認

```bash
python scripts/daily.py --mock       # 架空データで画像を作る（/tmp に出る）
python scripts/build.py --full       # 実データを取る
python scripts/daily.py --dry-run    # 実データで組み立て、中身を表示（送らない）
```

## 通知の設定

しきい値に触れたときだけLINEに飛ばせます。設定しなくてもサイトは動きます。

1. LINE Developers でMessaging APIのチャネルを作り、チャネルアクセストークンを取得
2. リポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
   - `LINE_CHANNEL_TOKEN` … チャネルアクセストークン
   - `LINE_TO` … 送信先のユーザーIDまたはグループID
3. 手元で確かめる場合は `python scripts/notify.py --dry-run`

通知条件は `scripts/notify.py` の `RULES` にまとめてあります。

| 項目 | 既定値 | 意味 |
|---|---|---|
| `z_threshold` | 2.5 | 値動きが何σを超えたら知らせるか |
| `corr_z_threshold` | 2.0 | 相関の異常が何σを超えたら知らせるか |
| `corr_cross_zero` | true | BTC×S&P500の30日相関が0を跨いだら知らせる |
| `risk_threshold` | 60 | リスク選好が±これを超えたら知らせる |
| `stale_alert` | true | 取得に失敗した銘柄が出たら知らせる |

15分ごとに同じ内容を送りつけないよう、前回送った「理由の組み合わせ」を
`docs/data/notify_state.json` に残し、変化が無ければ黙ります。
このファイルは朝の便りと共用で、月間の通数も一緒に数えています
（通知は最大120通まで。残りは朝の便りのぶん）。

**2026-09-03に直した不具合。** 以前は「送るものが無い回」に保存状態を
書き出していませんでした。このリポジトリは `docs` をまるごと配信し直すため、
書き出さない回の配信で公開中の `notify_state.json` が消え、
連投防止も月間上限も実質効いていませんでした。いまはどの枝を通っても必ず
書き出し、`daily.py --carry` で毎回引き継いでいます。

**LINE Notify について。** 以前広く使われていた LINE Notify は2025年3月末で
終了したと認識していますが、この点はご自身で確認してください。本スクリプトは
後継の Messaging API を使っています。すでに別の通知手段をお持ちなら、
`notify.py` の `send()` を差し替えるだけでそちらに流せます。
