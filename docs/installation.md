# インストールと起動

perpetui は TUI の本体が Python、起動用のコマンドが Go という構成です。

少し回りくどく見えるかもしれませんが、普段は `perpetui` という一つのコマンドで使えます。Go ランチャーが Python 環境を探して、Textual の画面を起動する仕組みです。

## 必要なもの

| 項目 | 用途 |
| --- | --- |
| Python 3.11 以上 | TUI 本体 |
| Go 1.23 以上 | Go ランチャーのビルド。インストール済みバイナリの実行には不要 |
| make | セットアップとインストール |
| ANSI / UTF-8 対応の端末 | チャートと画面の表示 |
| uv | Python 依存関係の取得。未導入なら venv + pip を使用 |
| pip 25.1 以上 | pip を使用する場合 |
| git | ソースコードの取得 |

120 列 × 40 行以上の端末を推奨しています。小さな端末でも動作しますが、複数チャートは表示領域が足りなくなります。

取引所のアカウント設定は必須ではありません。相場を見るだけなら認証せずに使えます。

## 普段使う環境にインストールする

一番簡単なのは `make install` です。

```sh
git clone https://github.com/haturatu/perpetui.git
cd perpetui
make install
```

既定では次の場所にインストールします。

| 配置先 | 内容 |
| --- | --- |
| `~/.local/bin/perpetui` | Go ランチャー |
| `~/.local/share/perpetui/venv` | アプリ専用の Python 環境 |
| `~/.local/share/bash-completion/completions/perpetui` など | シェル補完 |
| `~/.local/share/man/man1/` | man ページ |

`~/.local/bin` に PATH が通っていなければ追加してください。

```sh
export PATH="$HOME/.local/bin:$PATH"
perpetui --demo
```

`PREFIX`、`BINDIR`、`DATADIR` を指定すると配置先を変更できます。アンインストールには `make uninstall` を使います。

```sh
make install PREFIX="$HOME/.local"
make uninstall
```

インストールした後は `uv` の実行や Go のビルドは不要です。ただし、**インストール時に作成した専用の Python 環境は必要**です。バイナリだけを別の場所へ移した場合は、`make install` で入れ直してください。

## インストールせずに開発環境から使う

開発中ならローカルに `.venv` を作る方が扱いやすいです。

```sh
make setup
make run
```

`make setup` は uv が見つかれば `uv sync --locked --dev` を使います。なければ venv と pip でセットアップします。

uv を使う場合は、次のように直接起動できます。

```sh
uv sync --dev
uv run perpetui --demo
uv run perpetui
```

pip を使う場合も対応しています。

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/perpetui --demo
```

どちらを使うか明示したい場合は `make setup PY_TOOL=uv` または `make setup PY_TOOL=pip` とします。pip 経路でも `requirements.lock` と `requirements-dev.lock` の固定バージョンを使います。

### Makefile でできること

```sh
make setup         # Python 環境の準備
make build         # Go ランチャーを build/perpetui にビルド
make run           # ビルドして起動
make demo          # サンプルデータで起動
make test          # Python / Go のテスト
make lint          # Ruff / go vet / gofmt の検査
make completions   # Bash / Zsh / Fish / PowerShell の補完を生成
make man           # man ページを生成
make lock          # pip 用ロックファイルを更新
```

`make run` は実行のたびに `build` を呼び出します。ビルド済みのインストール版を使うだけなら、`perpetui` を直接実行した方が簡単です。

## アカウントなしでも使える

```sh
perpetui --demo-empty
perpetui --public
perpetui --symbol SOL --interval 1h --refresh 10
perpetui --account main
perpetui --no-websocket
```

`--demo-empty` はポジションなしのサンプル画面、`--public` は認証設定を使わず公開相場だけを表示します。`--no-websocket` では WebSocket を使わず REST で定期的に取得します。

アカウントが設定されていなければポジション欄を使わず、画面の領域をチャートに回します。ポジションが 0 件の場合も一覧を折りたたみます。

Hyperliquid はウォレットアドレス、Lighter はアカウント番号があれば秘密鍵なしで監視できます。MEXC のポジション監視には API 認証が必要です。詳細は[取引所の設定](exchanges.md)を参照してください。

## Python をどう探しているのか

Go ランチャーはインストール方法で Python の探し方を分けています。

- `make install`：インストール時に埋め込んだ専用 venv を使用
- `make build` の開発版：リポジトリ内または作業ディレクトリの `.venv` を使用
- `PERPETUI_PYTHON` を指定した場合：どちらでもその Python を優先

Unix では最終的に `exec` で Python に置き換えます。TUI の描画や取引所との通信は Python 側が担当します。

`perpetui --help`、`perpetui --version`、`perpetui completion ...`、`perpetui docs man` は Go だけで処理するため、Python を起動しません。

[README に戻る](../README.md)
