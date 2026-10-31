# 取引所と接続設定

## 取引所の選択

既定の取引所は Hyperliquid です。Lighter は `--exchange lighter`（または `lt`）、
MEXC は `--exchange mexc` を指定します。

```bash
uv run perpetui --exchange hyperliquid
uv run perpetui --exchange hyperliquid --public
uv run perpetui --exchange hyperliquid --symbol xyz:CL
uv run perpetui --exchange hyperliquid --demo
uv run perpetui --exchange hyperliquid --testnet

uv run perpetui --exchange lighter
uv run perpetui --exchange lighter --public
uv run perpetui --exchange lighter --symbol HYPE
uv run perpetui --exchange lighter --demo
uv run perpetui --exchange lighter --testnet

uv run perpetui --exchange mexc
uv run perpetui --exchange mexc --public
uv run perpetui --exchange mexc --symbol SOL_USDT
```

| 取引所 | ポジションがない場合 | 銘柄の例 | 数量の単位 |
| --- | --- | --- | --- |
| Hyperliquid | `BTC` | `BTC`、`HYPE`、`kPEPE`、`xyz:CL` | 銘柄の実数量 |
| Lighter | `BTC` | `BTC`、`HYPE`、`1000PEPE` | 銘柄の実数量 |
| MEXC | `BTC_USDT` | `BTC`、`ETH_USDT` | 契約枚数 |

Hyperliquid は通常の無期限先物と、HIP-3 のビルダーが提供する先物を対象にします。
Lighter は公開されている無期限先物（perp）を対象にします。
起動後は上部の取引所選択欄、または `e` で Hyperliquid・Lighter・MEXC を切り替えられます。
選択先の設定を読み直し、保有ポジションの先頭銘柄を表示します。
ポジションがなければ BTC を表示します。設定の読み込みに失敗した場合は元の画面を維持します。
`--account` と `--websocket-url` は起動時に選んだ取引所に適用し、
ほかの取引所には保存済みデフォルト設定や取引所別の環境変数を使用します。
`--public`・`--demo`・`--no-websocket` は切り替え後も適用されます。

![Hyperliquid の日本語デモ（サンプルデータ）](hyperliquid-ja.png)

## リアルタイム更新

### Hyperliquid

[Hyperliquid API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api) の
`candleSnapshot` で履歴を取得し、WebSocket の `candle` と `activeAssetCtx` で足・評価価格を更新します。
アカウントを設定した場合は `allDexsClearinghouseState` で全 DEX のポジションを受信します。
チャートの表示銘柄を変えても、ほかの保有銘柄の更新を続けます。

接続が切れた場合は REST で全 DEX の状態を再取得し、再接続します。
足の欠損も REST で補い、古い足やポジション通知による巻き戻りを防ぎます。
Hyperliquid の 24 時間高値・安値はこの API から取得できないため、`—` と表示します。

### Lighter

[Lighter API](https://docs.lighter.xyz/api/readme) の `candles` で履歴を取得し、
WebSocket の `candle` と `market_stats` で足・評価価格・24時間騰落率・売買代金を更新します。
アカウント番号を設定した場合は `account_all_positions` で保有ポジションを受信します。
どちらも公開チャネルで、秘密鍵・APIキー・認証トークンは使用しません。
チャートの表示銘柄を変えても、ほかの保有銘柄の更新を続けます。

接続が切れた場合は REST でポジションと足を再取得し、再接続します。
15秒ごとにpingを送信し、切断時は1〜30秒の間隔で再接続します。
Lighter の時間足は `1m / 5m / 15m / 30m / 1h / 4h / 1d` です。
Lighter のレバレッジはポジションの初期証拠金率から算出し、
クロスポジションの証拠金は初期証拠金の推定値を表示します。

### MEXC

通常起動では、RESTで直近160本の足を取得してから、
[MEXC先物WebSocket](https://www.mexc.com/api-docs/futures/websocket-api/native-ws-endpoint)
の `sub.kline` と `sub.ticker` を購読します。同じ時間の足は受信したOHLCVで置き換え、
次の時間の足は末尾へ追加します。出来高は累積値のため、受信ごとに足し込みません。
画面下部でチャートとポジションの更新時刻を確認できます。

足データとtickerは送信時刻を照合し、古いtickerで現在値を巻き戻さないようにしています。
未確定足も受信ごとに終値・高値・安値・出来高を更新します。

15秒ごとにpingを送信し、切断・受信停止時は1〜30秒の間隔で再接続します。
再接続時や足の欠損を検出した場合は、RESTで履歴を再取得します。
接続できない間も、既定で5秒ごとにRESTでチャートを更新します。
同じ銘柄・時間足を使う別の枠がなければ、銘柄・時間足の変更時に前の購読を終了します。

認証済みの場合、チャートとは別の接続で
[ポジション通知](https://www.mexc.com/api-docs/futures/websocket-api/position)
を購読し、新規建玉・数量変更・決済・建値・証拠金の変更を反映します。
所有銘柄すべての[評価価格](https://www.mexc.com/api-docs/futures/websocket-api/fair-price)
を購読し、価格が変わるたびにマーク価格・uPnL・ROE・合計uPnLを再計算します。
チャートに表示していない銘柄や両建ての各ポジションも対象です。
ポジションがない場合も変更通知を待機し、新規銘柄の価格購読を自動で追加します。

変更通知はバージョン・時刻を照合し、
古い通知で数量を巻き戻したり決済済みの行を復活させたりしないようにしています。
接続確立後にRESTで再同期して接続前・切断中の変更を補い、
接続中はポジションの定期 REST 取得を止めます。`r` はポジションを REST で再取得して接続も張り直します。
ポジション接続にも15秒ごとのpingと1〜30秒の再接続待機を使用します。

### 接続設定

`--refresh` は接続できない間のRESTフォールバックの更新間隔です（既定5秒）。
`--no-websocket` で常時REST更新に切り替えられます。デモもREST相当の定期更新です。

Hyperliquid の接続先は `https://api.hyperliquid.xyz` と `wss://api.hyperliquid.xyz/ws` です。
`HYPERLIQUID_BASE_URL`・`HYPERLIQUID_WS_URL`、または
`--hyperliquid-base-url`・`--websocket-url` で変更できます。
Lighter の接続先は `https://mainnet.zklighter.elliot.ai` と
`wss://mainnet.zklighter.elliot.ai/stream` です。
Lighter は `LIGHTER_BASE_URL`・`LIGHTER_WS_URL`、または
`--lighter-base-url`・`--websocket-url` で変更できます。
MEXC の接続先は `wss://contract.mexc.com/edge` が既定です。
`MEXC_WS_URL` または `--websocket-url` で変更できます。WebSocketの公開相場購読にAPIキーは不要です。
`--testnet` では選択した取引所の接続先をテストネットに切り替えます。

## Hyperliquid の設定

[参照 CLI の hl](https://github.com/haturatu/hl) で設定済みの場合は、
`~/.hl/hl.db` のデフォルトアカウントから監視先のアドレスを読み込みます。
データベースの作成・変更・マイグレーションは行いません。
API ウォレットの秘密鍵は読み込みません。ポジションの取得に必要なのは監視先アドレスだけです。

```bash
uv run perpetui --exchange hyperliquid --account main
uv run perpetui --exchange hyperliquid --hl-command /original/path/to/hl
```

復号にはアカウント保存時の `hl` コマンドの絶対パスを使用します。
通常は PATH から検出します。`HL_COMMAND`・`--hl-command` で明示でき、
データベースのディレクトリは `HL_HOME` で指定できます。
`--testnet` ではテストネット用アカウントを選択します。

保存済みアカウントがない場合は `HYPERLIQUID_WALLET_ADDRESS` を使用します。
`0x...` は監視する本体アカウントまたはサブアカウントの完全なアドレスに置き換えてください。

```bash
export HYPERLIQUID_WALLET_ADDRESS='0x...'
uv run perpetui --exchange hyperliquid
uv run perpetui --exchange hyperliquid --wallet-address "$HYPERLIQUID_WALLET_ADDRESS"
```

`--wallet-address` は保存済み設定より優先されます。
監視先を設定しない場合や `--public` を指定した場合は、公開相場のみを表示します。

## Lighter の設定

[参照 CLI の lighter](https://github.com/haturatu/ligher-xyz-cli) でアカウントを保存済みの場合は、
`~/.config/ligher-xyz-cli/accounts.db` のデフォルトアカウントから
ネットワーク別のアカウント番号を読み込みます。データベースの作成・変更・マイグレーションは行いません。
秘密鍵・APIキー・認証トークンは読み込みません。

```bash
uv run perpetui --exchange lighter --account main
uv run perpetui --exchange lighter --testnet
uv run perpetui --exchange lighter --lighter-config /path/to/accounts.db
```

`--account` で保存済みアカウント名を選択でき、`--lighter-account-index` は保存済み設定より優先されます。
`LIGHTER_CONFIG` でデータベースの場所、`LIGHTER_ACCOUNT_INDEX` でアカウント番号を指定できます。
`--testnet` ではテストネット用の保存済みアカウントを選択します。

```bash
export LIGHTER_ACCOUNT_INDEX=12345
uv run perpetui --exchange lighter
uv run perpetui --exchange lighter --lighter-account-index 12345
```

アカウント番号を設定しない場合や `--public` を指定した場合は、公開相場のみを表示します。

## MEXC の設定

[参照CLI](https://github.com/haturatu/mexc-cli) で `mexc account add` 済みなら、
`~/.mexc/mexc.db` のデフォルトアカウントを読み取ります。`MEXC_HOME` にも対応します。
データベースの作成・更新・マイグレーションは行いません。

既存CLIの暗号化キーはインストールされた `mexc` コマンドの絶対パスから導出されます。
通常はPATHから自動検出します。移動した場合は、アカウント保存時のパスを指定してください。

```bash
perpetui --exchange mexc --mexc-command /original/path/to/mexc
```

環境変数でも指定できます。両方設定されている場合は保存済みデフォルトより優先します。
`--account` を指定すると、その保存済みアカウントを優先します。

```bash
export MEXC_API_KEY='...'
export MEXC_API_SECRET='...'
perpetui --exchange mexc
```

認証設定がなければ公開相場モードでBTCを表示します。認証・通信エラーは画面下部に表示し、
最後に取得できたデータを残して再試行します。初回にポジション取得が失敗した場合もBTCを表示します。

[README に戻る](../README.md)
