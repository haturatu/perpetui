# インストール

## 依存関係

実行時:

- Python 3.11 以上
- Python パッケージ（[pyproject.toml](../pyproject.toml) とロックファイルで管理）
- ANSI / UTF-8 に対応した端末（120列 × 40行以上を推奨）
- 任意: `mexc-cli`・`hl`・`lighter` の保存済みアカウント、または各取引所の環境変数

ビルド・開発:

- Go 1.23 以上（Cobra ランチャーのビルド時のみ。インストール済みバイナリの実行には不要）
- `make`
- [uv](https://docs.astral.sh/uv/)（任意。ない場合は `venv` + pip を使用）
- pip 25.1 以上（pip フォールバック時）
- git（ソースを取得する場合）

## 起動

依存解決には [uv](https://docs.astral.sh/uv/) があると高速です。uv がない場合は venv + pip を使用します。

```bash
uv sync --dev
uv run perpetui --demo
uv run perpetui
```

pip を使用する場合:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/perpetui --demo
```

```bash
perpetui --demo-empty             # ポジションなし → BTCのサンプルチャート
perpetui --public                 # 認証設定を読まず公開相場のみ表示
perpetui --symbol SOL --interval 1h --refresh 10
perpetui --account main
perpetui --no-websocket           # チャート・ポジションともRESTで定期取得
```

認証情報が未設定の場合は、公開データでチャートを表示し、保有ポジション欄を非表示にします。
`--public` でも同じ表示になります。空いた領域はチャートに使用します。
認証情報が設定されていても、保有ポジションが 0 件の場合は一覧を折りたたみます。
Hyperliquid は秘密鍵なしでも、監視対象のウォレットアドレスがあればポジションを表示できます。
Lighter も秘密鍵や認証トークンなしで、アカウント番号だけでポジションを表示できます。

## Go ランチャーとインストール

正式な CLI エントリーポイントは Go 製の `perpetui` です。Go ランチャーは引数をそのまま
Python の Textual アプリへ渡し、Unix では `exec` でプロセスを置き換えます。
TUI 本体はこれまでどおり Python 実装です。

```bash
make setup          # .venv 作成 + Python 依存解決（uv があれば uv、なければ venv + pip）
make build          # Go バイナリを build/perpetui にビルド
make run            # Go ランチャー経由で TUI を起動
make demo           # サンプルデータで起動
make test           # Python / Go のテスト
make lint           # Ruff / go vet / gofmt
make completions    # Bash / Zsh / Fish / PowerShell 補完を生成
make man            # man ページを生成
make lock           # pip 用の requirements.lock / requirements-dev.lock を更新
```

依存解決は `uv` がインストールされていれば自動的に uv を使い、
なければ `python3 -m venv` と pip にフォールバックします。pip 経路でも
`requirements.lock` / `requirements-dev.lock` の固定バージョンを使用します。
明示する場合は次のように指定します。

```bash
make setup PY_TOOL=uv
make setup PY_TOOL=pip
```

ユーザー領域へのインストール:

```bash
make install
```

`~/.local/bin/perpetui`、`~/.local/share/perpetui/venv`、シェル補完、man ページを配置します。
`PREFIX` / `BINDIR` / `DATADIR` で配置先を変更できます。アンインストールは `make uninstall` です。

インストール後は Go バイナリだけで起動できます。実行時に `uv` は不要です。
`~/.local/bin` が PATH にない場合は追加してください。

```bash
export PATH="$HOME/.local/bin:$PATH"
perpetui --demo
perpetui --exchange lighter
perpetui --exchange hyperliquid --public
```

Python 環境の検出方法は、インストール版と開発版で分離されています。

- インストール版（`make install`）: `make install` 時に埋め込んだ専用 venv のみを使用
- 開発版（`make build` / `build/perpetui`）: リポジトリ内またはカレントディレクトリの `.venv`
- `PERPETUI_PYTHON` を指定した場合は、どちらでもその Python を最優先

インストール後に配置先を移動した場合は、埋め込まれた venv パスが変わるため
`make install` を再実行してください。

`perpetui --help`、`perpetui --version`、`perpetui completion ...`、`perpetui docs man` は
Python を起動せず Go 側だけで完結します。

[README に戻る](../README.md)
