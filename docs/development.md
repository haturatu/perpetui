# 開発


チャート描画は [Plotext 6](https://github.com/piccolomo/plotext) を使用しています。
ローソク足・出来高を上下のパネルに描画し、ANSI出力をRich Textに変換してTextualに表示します。
実体はPlotextの矩形、ヒゲは線分を使い、両者の列を揃えています。
FINEでは実体・ヒゲ・出来高に点字マーカー（1文字内を縦4×横2に分割）を使用します。
表示領域に合わせて直近の足を描画し、データ・建値・画面サイズが変わったときに再描画します。

APIは参照CLIと同様に [MEXC Futures API](https://www.mexc.com/api-docs/futures/integration-guide)
を利用し、[ドメイン更新案内](https://www.mexc.com/announcements/article/futures-api-access-domain-update-17827791532974)
に合わせて `https://api.mexc.com` を既定にしています。
`MEXC_FUTURES_BASE_URL`、`MEXC_BASE_URL`、`--futures-base-url` で変更できます。
注文・決済・アカウント変更の機能はありません。

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

`make setup / run / demo / check / build` でも実行できます。

ログはDEBUG/INFOをstdout、WARN/ERRORをstderrへ出力します。起動不能のエラーはstderrへ出力して
終了コード1で終了します。APIキー・署名・レスポンス本文はログに出しません。

## 構成とバージョン

責務と依存方向は [アーキテクチャ](architecture.md) を参照してください。
Python の実行時バージョンはインストール済みパッケージのメタデータから読み取ります。
Go ランチャーのバージョンは `pyproject.toml` から生成し、`make build-go` と `make build-install` でも同期します。
直接 `go build` する場合は、先に次を実行してください。

```bash
python3 scripts/sync_version.py
```

既定値・選択肢・英語ヘルプを含む Go/Python の CLI 契約と、生成済みバージョンはテストで照合します。
依存関係は uv と pip の両方に対応し、`uv.lock`・`requirements.lock`・`requirements-dev.lock` を維持します。

[README に戻る](../README.md)
