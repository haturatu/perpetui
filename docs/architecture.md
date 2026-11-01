# アーキテクチャ

perpetui は、相場を取得する処理とターミナルへ表示する処理を分けています。

最初から全部を Textual の画面に入れてしまうと、チャートを増やしたときに REST や WebSocket の購読が重複し、画面を切り替えるたびに通信をやり直すことになります。

なので、取引所ごとの違いをアダプターに閉じ込め、チャートの状態と相場の購読を別々に管理する構成にしました。

起動経路は次のとおりです。

```text
cmd/perpetui（Go CLI）
  → internal/launcher
  → perpetui.cli（Python CLI）
  → perpetui.ui.app（Textual）
```

## モジュールの役割

| 場所 | 担当するもの |
| --- | --- |
| `domain/` | Decimal を使った市場・ポジションモデル、時間足、CandleStore |
| `exchanges/base.py` | 取引所を共通に扱う DataSource Protocol |
| `exchanges/{mexc,hyperliquid,lighter}/` | REST、WebSocket、メッセージの解析、保存済みアカウント |
| `exchanges/websocket.py` | 接続再試行、バックオフ、heartbeat、終了処理 |
| `exchanges/account_crypto.py` | 既存 CLI の暗号化形式との互換処理 |
| `exchanges/factory.py` | 設定から取引所クライアントを生成 |
| `services/` | 相場の共有、ポジション更新、履歴取得、通信状態 |
| `ui/` | Textual の画面、レイアウト、ウィジェット、操作 |
| `internal/` | Go CLI と Python の検出・起動 |

`domain` と `services`、取引所アダプターは Textual、Rich、Plotext に依存しません。サービス層から特定の取引所実装を直接 import することも避けています。

こうしておくと、GUI を起動しなくても相場の状態や再接続処理をテストできます。依存方向はテストでも確認しています。

古い `perpetui.app.TradingApp` は互換用の入口です。新しいコードは `perpetui.ui.app.TradingApp` を使ってください。

## チャートと相場の購読を分ける

各チャートは `ChartSession` と `ChartPanel` で構成します。

`ChartSession` がデータ・購読・履歴を管理し、`ChartPanel` は受け取った状態を画面に描画します。`TradingApp.charts` にセッションを持ち、操作対象は `WorkspaceState.active_chart_id` で指定します。

### 同じ銘柄を何度も取得しない

複数の枠に同じ銘柄と時間足を表示した場合、枠の数だけ API を叩くのは無駄です。

`MarketFeedHub` は取引所クライアントごとに作成して、同じ銘柄・時間足の同時 REST 取得とストリーム購読を共有します。最後の購読者がいなくなった時点で、そのストリームを終了します。

受信待ちには最新の完全な市場スナップショットを保持し、表示の遅い枠があってもキューが増え続けないようにしています。

また、ある枠が取得をキャンセルしても、別の枠が待っている共有リクエストまでキャンセルしません。逆に、**履歴カーソルや価格軸の倍率、描画形式は各枠で個別に管理**します。

異なる銘柄を一つの WebSocket 接続へまとめるような多重化はしていません。共有の単位は同じ銘柄・時間足の購読です。

### 履歴の取得

`HistoryService` はクライアント内で履歴キャッシュと取得中のバックフィルを共有します。ある枠が過去の足を読み込んだ場合、別の枠からも利用できます。

`ChartSession` は銘柄や時間足を変えるたびに世代番号を更新します。変更前の遅いレスポンスが後から届いても、新しいチャートを上書きしないためです。

`PositionsService` もリビジョンを比較して、WebSocket で受け取った新しいポジションを遅い REST 応答で巻き戻さないようにしています。

## 取引所を切り替えるとき

取引所を切り替える際には、画面の取得ワーカー、各 `ChartSession`、ポジション購読、履歴取得、共有フィードを終了してから古いクライアントを閉じます。

新しい取引所には別の Hub と Service を作り直します。前の取引所の接続やアカウント状態を持ち越さないようにするためです。

## 表示とレイアウト

`ui/app.tcss` はスタイル、`ui/workspace.py` はチャート選択、表示数、配置、同期などの操作です。

`ui/layout.py` は Textual の Widget に依存しない計算部分です。Auto 配置では、枠数と端末の幅・高さから列数・段数・スパンを決めます。

リサイズ、最大化、配置の変更でウィジェットを作り直さないのも重要です。レイアウトだけを変更するため、チャートの履歴やストリームを保持できます。

`ui/widgets/positions.py` はポジション一覧の行更新と、件数に応じた領域の伸縮を担当します。0 件で折りたたむ処理や、多い場合のスクロールもここにあります。

## テストと配布

テストは `tests/domain`、`tests/exchanges`、`tests/services`、`tests/ui`、`tests/integration` に分けています。

WebSocket のプロトコルはローカルのテストサーバーで検証し、実際の取引アカウントを必要としません。統合テストではリサイズ、最大化、銘柄変更、取引所切替、接続共有と終了処理を確認します。

Python のバージョンと Go の生成済みバージョン、CLI のオプション・既定値・英語ヘルプもテストで照合します。

uv と pip の両方でインストールするため、`uv.lock` だけでなく pip 用のロックファイルも管理しています。

[README に戻る](../README.md)
