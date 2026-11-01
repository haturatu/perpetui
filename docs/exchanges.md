# 取引所と接続設定

perpetui は Hyperliquid、Lighter、MEXC の先物チャートとポジションを表示できます。

ただし、同じ「ポジション表示」でも、必要な設定は取引所によって違います。Hyperliquid と Lighter は公開情報だけで監視できますが、MEXC は認証が必要です。

さらに MEXC の数量は契約枚数、ほかの二つは実数量です。表示を比較するときは、この違いにも注意してください。

## まずは取引所を選ぶ

何も指定しなければ Hyperliquid です。

```sh
perpetui --exchange hyperliquid
perpetui --exchange hyperliquid --public
perpetui --exchange hyperliquid --symbol xyz:CL
perpetui --exchange hyperliquid --demo
perpetui --exchange hyperliquid --testnet

perpetui --exchange lighter
perpetui --exchange lighter --public
perpetui --exchange lighter --symbol HYPE
perpetui --exchange lighter --demo
perpetui --exchange lighter --testnet

perpetui --exchange mexc
perpetui --exchange mexc --public
perpetui --exchange mexc --symbol SOL_USDT
```

`lighter` は `lt` と省略できます。

| 取引所 | ポジションがないときの既定銘柄 | 銘柄の例 | 数量の単位 |
| --- | --- | --- | --- |
| Hyperliquid | `BTC` | `BTC`、`HYPE`、`kPEPE`、`xyz:CL` | 実数量 |
| Lighter | `BTC` | `BTC`、`HYPE`、`1000PEPE` | 実数量 |
| MEXC | `BTC_USDT` | `BTC`、`ETH_USDT` | 契約枚数 |

Hyperliquid は通常の無期限先物に加えて、HIP-3 のビルダーが提供する先物にも対応しています。Lighter は公開されている無期限先物を対象にしています。

起動した後でも、上部の取引所選択欄や `e` キーから切り替えられます。そのときは新しい取引所の設定を読み直し、保有ポジションがあれば先頭の銘柄を表示します。取得に失敗した場合は元の画面を維持します。

起動時の `--account` と `--websocket-url` は、最初に選んだ取引所にだけ適用します。切り替え後は取引所別の保存済み設定や環境変数を使います。一方、`--public`、`--demo`、`--no-websocket` は切り替え後も有効です。

![Hyperliquid の日本語デモ（サンプルデータ）](hyperliquid-ja.png)

## Hyperliquid

### どうやって相場を取得しているのか

[Hyperliquid API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api) の `candleSnapshot` で履歴を取得し、WebSocket の `candle` と `activeAssetCtx` でチャートと評価価格を更新します。

ウォレットアドレスを設定した場合は `allDexsClearinghouseState` から全 DEX のポジションを監視します。表示中のチャートを切り替えても、ほかの保有銘柄の更新は止めません。

切断時は REST でポジションと足を取り直してから再接続します。足の欠損も REST で補い、古い通知でデータを巻き戻さないようにしています。

なお、この API から 24 時間高値・安値を取得できないため、該当欄は `—` になります。

### 監視するアドレスを指定する

[hl CLI](https://github.com/haturatu/hl) を使用している場合は、`~/.hl/hl.db` のデフォルトアカウントを読み込めます。データベースを作成・変更・マイグレーションすることはありません。

```sh
perpetui --exchange hyperliquid --account main
perpetui --exchange hyperliquid --hl-command /original/path/to/hl
```

保存済みの情報を読む際、必要な場合はアカウント保存時の `hl` コマンドの絶対パスを使います。`HL_COMMAND` または `--hl-command` で指定でき、データベースの場所は `HL_HOME` で変更できます。

ポジションの取得自体に秘密鍵は必要ありません。監視対象のウォレットアドレスが分かっていれば、次のようにも指定できます。

```sh
export HYPERLIQUID_WALLET_ADDRESS='0x...'
perpetui --exchange hyperliquid
perpetui --exchange hyperliquid --wallet-address "$HYPERLIQUID_WALLET_ADDRESS"
```

`0x...` は実際のアカウントまたはサブアカウントのアドレスに置き換えてください。`--wallet-address` は保存済み設定より優先されます。

`--testnet` の場合はテストネット側の保存済みアカウントを選びます。監視先を指定しない場合や `--public` を付けた場合は、公開相場だけになります。

## Lighter

### 相場とポジションの更新

[Lighter API](https://docs.lighter.xyz/api/readme) の `candles` で履歴を取得し、WebSocket の `candle` と `market_stats` で足・評価価格・24 時間騰落率・売買代金を更新します。

アカウント番号が分かれば、`account_all_positions` からポジションを確認できます。**秘密鍵、API キー、認証トークンは使いません**。どちらも公開チャネルです。

切断した場合は REST で足とポジションを再取得します。15 秒ごとに ping を送り、切断時は 1〜30 秒の間隔で再接続します。

Lighter の時間足は `1m`、`5m`、`15m`、`30m`、`1h`、`4h`、`1d` です。`1w` には対応していません。

レバレッジは初期証拠金率から計算し、クロスポジションの証拠金は初期証拠金の推定値です。取引所から厳密な同一値が得られるわけではない点に注意してください。

### 監視するアカウントを指定する

[既存の lighter CLI](https://github.com/haturatu/ligher-xyz-cli) で設定している場合は、`~/.config/ligher-xyz-cli/accounts.db` のデフォルトアカウントから番号を読み込みます。

```sh
perpetui --exchange lighter --account main
perpetui --exchange lighter --testnet
perpetui --exchange lighter --lighter-config /path/to/accounts.db
```

`LIGHTER_CONFIG` でファイルの場所、`LIGHTER_ACCOUNT_INDEX` または `--lighter-account-index` で監視先の番号を指定できます。明示した番号は保存済み設定より優先されます。

```sh
export LIGHTER_ACCOUNT_INDEX=12345
perpetui --exchange lighter
perpetui --exchange lighter --lighter-account-index 12345
```

データベースの作成や変更は行いません。`--testnet` ではテストネットのアカウントを使用します。設定がなければ公開相場だけを表示します。

## MEXC

### REST と WebSocket

MEXC は、まず REST で直近 160 本の足を取得し、その後 [先物 WebSocket](https://www.mexc.com/api-docs/futures/websocket-api/native-ws-endpoint) の `sub.kline` と `sub.ticker` を購読します。

同じ時刻の足は受信した OHLCV で置き換え、次の足は末尾へ追加します。出来高は累積値なので、更新が届くたびに加算しません。

接続は 15 秒ごとに ping を送信し、切断や受信停止があれば 1〜30 秒の間隔で再接続します。欠けている足は REST で補います。WebSocket に接続できない間も、既定では 5 秒ごとに REST でチャートを更新します。

相場の公開購読だけなら API キーは不要です。

### ポジションを表示する

MEXC のポジション監視には API キーとシークレットが必要です。

認証できた場合はチャートとは別の WebSocket で[ポジション通知](https://www.mexc.com/api-docs/futures/websocket-api/position)を購読します。新規建玉、数量、決済、建値、証拠金などが変化したら画面へ反映します。

さらに、保有している全銘柄の[評価価格](https://www.mexc.com/api-docs/futures/websocket-api/fair-price)を購読し、マーク価格、uPnL、ROE、合計 uPnL を更新します。画面に表示していない銘柄や両建てのポジションも対象です。

古い通知によって数量を巻き戻したり、決済したポジションを復活させたりしないよう、通知のバージョンと時刻を照合しています。接続直後は REST でもう一度状態を合わせ、その後は定期 REST 取得を止めます。

`r` キーを押すと、REST でポジションを取り直し、接続も張り直します。接続できない間も、最後に取得できた情報を残して再試行します。

### API キーの設定

[mexc-cli](https://github.com/haturatu/mexc-cli) でアカウントを保存していれば、`~/.mexc/mexc.db` のデフォルトアカウントから読み取れます。`MEXC_HOME` にも対応しています。

既存 CLI の暗号化キーは、インストール済み `mexc` コマンドの絶対パスから導出されています。コマンドを移動した場合は、**保存したときのパス**を指定してください。

```sh
perpetui --exchange mexc --mexc-command /original/path/to/mexc
```

環境変数から直接指定することもできます。キーとシークレットを両方指定した場合は保存済みデフォルトより優先されます。`--account` を指定したときは、その保存済みアカウントを優先します。

```sh
export MEXC_API_KEY='...'
export MEXC_API_SECRET='...'
perpetui --exchange mexc
```

実際のキーをシェル履歴、Issue、ログなどに貼り付けないでください。perpetui 自体は注文・決済を行いませんが、API キーの権限は別途制限しておく方が安全です。

設定がない場合は公開相場のみになり、初期銘柄は BTC です。

## 接続先と更新間隔

接続先を変更したい場合は、取引所ごとの環境変数か CLI オプションを使います。

| 取引所 | 既定の REST / WebSocket | 上書き方法 |
| --- | --- | --- |
| Hyperliquid | `https://api.hyperliquid.xyz` / `wss://api.hyperliquid.xyz/ws` | `HYPERLIQUID_BASE_URL`、`HYPERLIQUID_WS_URL`、`--hyperliquid-base-url`、`--websocket-url` |
| Lighter | `https://mainnet.zklighter.elliot.ai` / `wss://mainnet.zklighter.elliot.ai/stream` | `LIGHTER_BASE_URL`、`LIGHTER_WS_URL`、`--lighter-base-url`、`--websocket-url` |
| MEXC | `wss://contract.mexc.com/edge`（WebSocket） | `MEXC_WS_URL`、`--websocket-url` |

`--refresh` は、WebSocket を利用できない場合の REST 更新間隔です。既定は 5 秒です。`--no-websocket` を指定すると、最初から REST による定期更新へ切り替えます。

`--testnet` は、選択した取引所の接続先をテストネット用に変更します。取引所ごとに利用できる機能は異なります。

[README に戻る](../README.md)
