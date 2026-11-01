# 開発

perpetui は Python と Go の両方を使っていますが、チャートの描画や取引所との通信は Python 側です。Go は CLI の入口と Python の起動を担当しています。

なぜ分けているかというと、普段の起動やシェル補完、man ページの生成は Go でまとめつつ、TUI 本体は Textual の仕組みを使いたかったためです。

## テストと整形

リポジトリのルートで、次のコマンドを実行します。

```sh
make setup
make check
```

`make check` は Python の Ruff とテスト、Go の `go vet`・`gofmt`・テストを実行します。個別に動かす場合は以下です。

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

`make test`、`make lint`、`make build`、`make demo` なども用意しています。使い方は[インストール](installation.md)を参照してください。

### ログ

DEBUG / INFO は標準出力、WARN / ERROR は標準エラーへ出力します。起動できない場合は標準エラーへ理由を出し、終了コード 1 で終了します。

API キー、署名、レスポンス本文はログに出さない設計です。Issue を作る際も、認証情報をそのまま貼り付けないでください。

## チャートの描画

チャートには [Plotext 6](https://github.com/piccolomo/plotext) を使用しています。ANSI の描画結果を Rich Text に変換し、Textual のウィジェットへ表示します。

ローソク足の実体は矩形、ヒゲは線分です。FINE モードでは点字マーカーを使用し、1 文字の横 2 × 縦 4 の領域を使って実体・ヒゲ・出来高を描画します。

描画領域に合わせて直近の足を選び、データ、建値、端末サイズが変わったときに再描画します。端末フォントによって点の見え方は変わります。

MEXC の API は [MEXC Futures API](https://www.mexc.com/api-docs/futures/integration-guide) を使用しています。[接続ドメインの更新案内](https://www.mexc.com/announcements/article/futures-api-access-domain-update-17827791532974)に合わせ、REST の既定は `https://api.mexc.com` です。`MEXC_FUTURES_BASE_URL`、`MEXC_BASE_URL`、`--futures-base-url` で変更できます。

アプリは読み取り専用です。注文、決済、アカウント変更の処理は実装していません。

## Go と Python のバージョン

Python 側のバージョンはインストールしたパッケージのメタデータから取得します。一方、Go のバージョン表示には `pyproject.toml` の値を生成して埋め込んでいます。

`make build-go` と `make build-install` は事前に同期処理を実行しますが、`go build` を直接呼ぶ場合は自分で同期してください。

```sh
python3 scripts/sync_version.py
go build ./cmd/perpetui
```

Go と Python の CLI オプションについては、名前・既定値・選択肢・英語ヘルプが一致しているかテストで確認しています。

依存関係は uv と pip の両方を扱うため、`uv.lock`、`requirements.lock`、`requirements-dev.lock` を維持しています。ロックファイル更新には `make lock` を使えます。

## 内部構成

各モジュールの責務と依存方向、共有 WebSocket や複数チャートの状態管理については[アーキテクチャ](architecture.md)を参照してください。

[README に戻る](../README.md)
