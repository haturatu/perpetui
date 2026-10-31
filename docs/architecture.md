# アーキテクチャ

perpetui は Python の Domain・取引所アダプター・Service・UI と、Go ランチャーに分かれます。
アプリの起動経路は `cmd/perpetui` → `internal/launcher` → `perpetui.cli` → `perpetui.ui.app` です。

## 責務と依存方向

| 場所 | 責務 | 依存先 |
| --- | --- | --- |
| `domain/` | Decimal による市場・ポジションモデル、時間足の秒数、CandleStore | 標準ライブラリ |
| `exchanges/base.py` | DataSource Protocol | Domain |
| `exchanges/{mexc,hyperliquid,lighter}/` | REST、購読プロトコル、メッセージ解析、保存済みアカウント | Domain、共通エラー・翻訳・通信処理 |
| `exchanges/websocket.py` | 再接続・バックオフ・heartbeat の終了処理、共通デコード | 共通エラー |
| `exchanges/account_crypto.py` | 既存 CLI の暗号化形式との互換処理 | 共通エラー |
| `exchanges/factory.py` | 起動設定から取引所クライアントを生成 | DataSource、取引所アダプター |
| `services/` | 相場購読、各チャートの通信状態、ポジション更新、非同期履歴取得 | Domain、DataSource |
| `ui/` | Textual のイベント・画面状態・レイアウト・ウィジェット・表示書式 | Domain、Service、DataSource |
| `internal/` | Go の CLI、Python の検出と起動 | Go のみ |

Domain・Service・取引所アダプターは Textual・Rich・Plotext に依存しません。
Service は特定取引所の実装を import しません。この依存方向はテストで検査します。
`perpetui.app.TradingApp` は `perpetui.ui.app.TradingApp` への互換入口です。
旧フラットモジュールの import は表の各パッケージへ移行してください。

## チャートと共有フィード

すべての枠は `ChartSession` と `ChartPanel` を使います。最初の枠にも同じ取得・停止・ストリーム処理を適用します。
`TradingApp.charts` にセッションを保持し、操作対象は `WorkspaceState.active_chart_id` で指定します。
`ChartPanel` はデータ変更をウィジェットへ反映し、通信は `ChartSession` に委ねます。

`MarketFeedHub` はクライアントごとに作成し、同じ銘柄・時間足の REST 取得を同時実行中は共有します。
ストリームは購読者へ更新を配信し、最後の購読者が離れたときに終了します。
受信待ちキューには最新の完全な市場スナップショットを保ち、遅い枠によるメモリ増加を防ぎます。
一方の枠の取得キャンセルで、もう一方が待つ共有リクエストを取り消しません。

各枠は固有の CandleStore・履歴カーソル・表示形式・拡大率を保持します。
共通フィードの更新で、別の枠の選択状態を上書きしません。
異なる銘柄の購読を単一ソケットにまとめる処理は行いません。

## 状態変更と終了

ChartSession は銘柄・時間足の変更時に世代を更新し、以前の取得結果を採用しません。
PositionsService はリビジョンを照合し、復旧したストリームを遅い REST 応答で巻き戻しません。
HistoryService はクライアント内で履歴キャッシュと実行中のバックフィルを共有します。

取引所切替では、ウィジェットの取得ワーカー、各セッション、ポジション購読、履歴取得、共有フィードを終了してから、元のクライアントを閉じます。
新しいクライアントには別の Hub・Service を用意し、古いアカウントのデータや接続を引き継ぎません。

## 表示

`ui/app.tcss` は画面のスタイル、`ui/workspace.py` はチャート選択・表示数・配置・同期操作を担当します。
Workspace は Textual の MessagePump を使ったイベント用 mixin で、アプリが提供するサービスを利用します。
`ui/layout.py` は Widget に依存しない配置計算です。
配置・最大化・端末サイズ変更では既存の枠を保持し、列数・段数・スパンだけを変更します。
`ui/widgets/positions.py` はテーブルの行更新と表示領域の伸縮を担当します。

## 検証と配布

テストは `tests/domain`、`tests/exchanges`、`tests/services`、`tests/ui`、`tests/integration` に分けています。
取引所の購読プロトコルはローカル WebSocket サーバーで検証し、実際のアカウントを使用しません。
統合テストはリサイズ・最大化・銘柄変更・取引所切替・接続共有と終了を検証します。

Python の配布メタデータと Go の生成済みバージョンは `pyproject.toml` に合わせます。
Go/Python の CLI 契約ではオプション名・既定値・選択肢・英語ヘルプを照合します。
`uv.lock` と pip 用のロックファイルは両方のインストール経路に必要です。

[README に戻る](../README.md)
