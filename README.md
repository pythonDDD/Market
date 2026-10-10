# 潮目 SHIOME 2.1

世界の市場を、やさしく深く。GitHub Pages + GitHub Actions の構成を維持した全面改修版です。Python 3.12（検証環境 3.12.14）、Python外部パッケージ不要。サイトのビルドにもnpmは不要です。

**本番は `main` への更新をGitHub Actionsで検証・配信します。設定と手動診断は `導入手順.md` を参照してください。**

## 今回の改修

- ラベンダーとミントのUI、案内役「しおちゃん」、スマホ対応、ライト／ダーク切替。
- ダッシュボード、マーケット、お気に入り、詳細分析、通知、ノートの6画面。
- 主要4市場のカード、12市場のヒートマップ、市場の気分、異常スキャン。
- 市場の検索／分類／並べ替え／CSV出力。お気に入りはブラウザ保存。
- 共通する日付だけを使う比較チャート。起点100、1か月／3か月／1年、値のツールチップ。
- 既存のローソク足、相関、リード・ラグ、曜日・月、分布、類似局面、円建て比較、資金調達レートを維持。
- ブラウザ専用の価格アラート、マーケットノート、JSONバックアップ／復元。
- LINEの認証・月間送信枠の診断と送信結果を画面とActions Summaryに表示。

## LINEが届かなかった理由

2026-10-10の公開状態と添付コードを確認しました。

1. `daily.py` は画像を作る回と送信する回が別でした。公開 `notify_state.json` の当日 `rendered_at` は **10:01:17 JST**、当日の `attempts` は0でした。後続のActions実行は11時台で、旧コードの **11:00締切** により送信対象から外れます。この日の未送信を説明できる組合せです。
2. 旧 `update.yml` は通知ステップを `continue-on-error: true` にしており、後で失敗を確認する処理がありませんでした。送信が失敗してもワークフローは緑になります。
3. コードが受け付ける旧Secret名を、ワークフローが環境変数に渡していませんでした。旧名だけ登録した場合は未設定扱いになります。

トークン失効、実際のSecret登録内容、ブロック有無は確認していません。全期間の未送信原因を一つに断定してはいません。

## 新しい通知の流れ

`notification.py` が唯一の本番送信入口です。

1. 設定、Bot情報、LINE側の月間送信枠を確認。
2. `market-state` ブランチから送信済み記録を取得。初回はLINE設定の有無にかかわらず、サイト配信前に旧公開状態を移行。移行失敗時は公開状態を消さないよう配信を止めます。
3. 日本時間9時以降、まだ当日分が未送信なら、**その実行内で本文を1通送信**。画像生成・公開待ちなし。11時の打ち切りもありません。
4. 送信前に本文と再試行キーを保存。通信断や5xxの再試行は同じ本文・同じキーで実行。
5. 受付成功後に送信済み記録を保存。APIが同じキーを受付済みなら、重複として扱い再送しません。
6. 公開用 `docs/data/line_status.json` には診断コード・日時・送信数だけを書きます。トークンや宛先IDは出しません。
7. 通知失敗でもサイトの更新を試みますが、最後にワークフローを失敗扱いにします。診断がサイトへ配信できない場合も、Actionsの成果物に残します。

朝の定期便と異常通知が同時に該当したときは、1通にまとめます。異常通知の同じ条件は90分の間隔と24時間の同一条件抑制を適用します。

月間上限190通・異常通知120通は**アプリ側の予算**です。LINEプランの上限ではありません。LINE側の実際の利用数・上限はAPIで取得します。グループ送信などではアプリのリクエスト数とLINEの課金通数は一致しません。

**API受付はスマートフォンへの配達確認ではありません。** 友だち追加やブロック状態などにより、APIが成功しても届かないことがあります。

## 設定

GitHub → Settings → Secrets and variables → Actions → Secrets:

| 項目 | 意味 | 互換の旧名 |
|---|---|---|
| `LINE_TOKEN` | Messaging API チャネルアクセストークン | `LINE_CHANNEL_TOKEN` / `LINE_CHANNEL_ACCESS_TOKEN` |
| `LINE_USER_ID` | 自分のユーザーID。`U`で始まる33文字 | `LINE_TO` |

`C`で始まるグループID、`R`で始まるルームIDも形式として受け付けます。勝手に一斉配信へ切り替える処理はありません。

同じ場所の **Variables** に任意で指定:

| 項目 | 初期値 | 意味 |
|---|---|---|
| `LINE_ENABLED` | `true` | `false`にするとLINE通知を止める |
| `LINE_DAILY_HOUR` | `9` | 日本時間の開始時刻。0〜23 |

それ以外のしきい値・上限は `market.config.json` の `notifications` で変更します。画面のブラウザ専用価格アラートは、このサーバー側LINE設定とは別です。

## データ品質・計算の修正

- 5日・20日変化率を日次変化率の加算から、終点÷起点−1の複利リターンに修正。利回りは従来どおり差分（pt）。
- 年初来の基準を前年末の最後の観測値へ修正。前年末を含む履歴がなければ欠損を表示。
- Coinbaseの日足取得に `start` と `end` を両方指定。旧コードの `end` だけの指定では範囲が無視される仕様に対応。最大300本ずつ、約2年分を取得。
- Krakenへの切替時に終値辞書とOHLC辞書を正しく取り出す。Krakenの未確定の最後の足を除外。
- Coinbaseの当日進行中の足を除外。Yahooは取引所タイムゾーンと取引終了時刻を使い、進行中の日足を分析から外す。休場・特別セッションなどは取得元のメタ情報に依存します。
- キャッシュを再利用するときにも、前回の取得失敗を「正常」に書き換えません。
- 合成系列を取得件数に混ぜません。日足の取得状態と現値の取得状態を区別してJSONに記録。
- 全取得先が失敗した場合は配信を止め、前の公開サイトを保ちます。
- Yahooの日足取得を最大4件並列化。レート制限時に無制限な再試行はしません。
- 疎通確認ワークフローが存在しない `healthcheck.py` を呼んでいた点を、実在する `healthcheck2.py` に修正。

値動きの相関は因果を示しません。類似局面や季節性は予測の保証ではありません。銘柄により観測期間が違い、暗号資産と株式は取引日・日足の区切りも異なります。

## ローカルでの確認

```powershell
# 既存のPython 3.12.14仮想環境を利用
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
python scripts/build.py --selftest
python scripts/chart.py --selftest
python scripts/daily.py --selftest
python scripts/healthcheck2.py --selftest
# 実データを取得（通知は送らない）
python scripts/build.py --full --seed-url https://pythonddd.github.io/Market/data/history.json
# 通知内容だけ確認。ネット接続・状態変更・送信なし
python scripts/notification.py --dry-run
# 自分のPCでサイトを開く
python scripts/preview.py
```

フロントエンドの計算テスト（Nodeがある場合）:

```text
node --test tests/core.test.cjs
node --check docs/app.js
node --check docs/dashboard.js
```

実際にテスト送信したい場合は環境変数を設定して `python scripts/notification.py --test`。認証・送信枠だけなら `--check`。トークンを公開ファイルやJavaScriptへ書かないでください。

## ファイル構成

```text
.github/workflows/update.yml       更新・通知・Pages配信
.github/workflows/healthcheck.yml  手動の疎通確認
market.config.json                LINEしきい値・上限
scripts/notification.py            通知の統合入口
scripts/line_transport.py          LINE API、診断、再試行
scripts/state_store.py             永続送信状態・送信待ち本文
scripts/build.py                  取得・分析の統合
scripts/sources.py                データソース
scripts/analytics.py              統計計算
scripts/daily.py                   市場だより本文と手動チャート描画
scripts/notify.py                  異常スキャンの通知本文
scripts/preview.py                 手元での確認用サーバー
scripts/make_preview.py            保存データを埋め込んだHTMLを生成
scripts/chart.py                   既存PNGチャートエンジン
docs/                             GitHub Pages配信物
  core.js                         検証可能なUI計算・データ操作
  dashboard.js / dashboard.css     新画面
  app.js / style.css               既存詳細分析（改修済み）
tests/                            通知・計算・取得の回帰テスト
PREVIEW.html                      保存データを埋め込んだ画面見本
導入手順.md / 検証結果.md          導入・確認できた範囲
```

旧 `潮目_手引き.pptx` は元の添付をそのまま残しています。旧通知仕様の説明を含むため、今回の導入にはこのREADMEと導入手順を使ってください。

## 公式仕様の参照

- LINE Messaging API: https://developers.line.biz/en/reference/messaging-api/
- 同一キーによる再試行、受付と配達の違い、24時間の有効期限: https://developers.line.biz/en/docs/messaging-api/retrying-api-request/
- GitHub Actions のschedule遅延・実行省略: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHubの状態ファイル更新: https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents
- GitHub Git trees / commits: https://docs.github.com/en/rest/git/trees / https://docs.github.com/en/rest/git/commits
- Coinbaseの時間範囲と300本上限: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- Krakenの未確定足: https://docs.kraken.com/api-reference/market-data/get-ohlc-data

## 2.1 — 読みやすい配色

- 白一色の面をグレージュにし、本文は濃い墨色、アクセントは深い紫・緑へ変更。
- 補足文字のコントラストは通常の背景に対して4.5:1以上。薄いフォントを避け、説明・数値・ラベルを拡大。
- SVGの文字、相関表、数式の背景もライト／ダークの両テーマに追従。
- スマートフォンでは異常スキャンを段組みにし、横長チャートは文字を潰さずスクロール表示。
- `PREVIEW.html` は生成物のためGitには保存しません。必要なら実データ取得後に `python scripts/make_preview.py` で生成できます。
