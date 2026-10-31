# perpetui

Hyperliquid・Lighter・MEXC の先物チャートと保有ポジションを表示するターミナルアプリです。
Python と Textual で画面を描画し、Go ランチャーから起動します。注文・決済・アカウント変更の機能はありません。

![デモ画面（サンプルデータ）](docs/demo.png)

## 起動

Python 3.11 以上、Go 1.23 以上、make、ANSI / UTF-8 対応端末を用意してください。
120 列 × 40 行以上を推奨します。Python の依存解決には uv を使用し、未導入の場合は venv と pip に切り替えます。

```bash
make install
perpetui --demo
```

既定では `~/.local/bin/perpetui`、専用の Python 環境、シェル補完、man ページをインストールします。
`~/.local/bin` を PATH に追加してください。開発環境では次のコマンドでも起動できます。

```bash
make setup
uv run perpetui --demo
```

配置先の変更、pip を使う手順、Python 環境の検出方法は [インストール](docs/installation.md) を参照してください。

## 取引所

既定の取引所は Hyperliquid です。認証設定がない場合は公開相場を表示します。
保存済みアカウントまたは環境変数から監視先を設定すると、保有ポジションも表示します。

```bash
perpetui --public
perpetui --exchange lighter
perpetui --exchange mexc
perpetui --symbol SOL --interval 1h --lang ja
```

| 取引所 | ポジションの監視設定 | 数量の単位 |
| --- | --- | --- |
| Hyperliquid | ウォレットアドレス、または hl の保存済みアカウント | 実数量 |
| Lighter | アカウント番号、または lighter の保存済みアカウント | 実数量 |
| MEXC | API キーとシークレット、または mexc-cli の保存済みアカウント | 契約枚数 |

アカウントの読み込み順、環境変数、接続先、テストネットは [取引所と接続設定](docs/exchanges.md) を参照してください。

## 操作

| キー | 操作 |
| --- | --- |
| `?` | ショートカット一覧 |
| `Ctrl + 1〜9` | チャートの表示数を変更 |
| `1`〜`9` | 操作するチャートを選択 |
| `l` | Auto / Horizontal / Grid / Vertical の配置選択 |
| `/` | 銘柄検索 |
| `e` | 取引所を切り替え |
| `[` / `]` | 選択チャートの時間足を変更 |
| `←` / `→`、`End` | 履歴を移動、最新足へ戻る |
| `z` | 選択チャートを最大化、元に戻す |
| `p`、`Shift + P` | ポジション欄を手動切替、自動表示へ復帰 |
| `r` | 即時更新 |
| `q` / `Ctrl + C` | 終了 |

Auto は描画領域に応じて枠を配置します。ポジション欄は行数に応じて伸縮し、0 件では折りたたみます。
同じ銘柄・時間足の枠は相場購読を共有し、履歴カーソル・拡大率・表示形式は各枠で保持します。

時間足・カーソルの同期、表示形式、日本語と英語の切替、Konsole のキー設定は [操作](docs/usage.md) を参照してください。
全オプションは `perpetui --help` または `man perpetui` で確認できます。

## 開発

```bash
make check
uv build
```

[開発](docs/development.md) に検証コマンドとログ設計、[アーキテクチャ](docs/architecture.md) にモジュールの責務をまとめています。
