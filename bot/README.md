# ビットコイン自動売買ボット（ドライラン版）

GitHub Actions の cron で定期的に起動し、ローソク足を見て売買判断を 1 回だけ行う。
**初期設定は完全なドライラン**で、取引所に注文は一切飛ばない。

## つくり

| ファイル | 役割 |
| --- | --- |
| `config.yml` | 取引所・戦略・リスク設定。基本ここだけ触る |
| `market.py` | 公開 API からローソク足を取得（API キー不要） |
| `strategy.py` | シグナル生成（`sma_cross` / `dca`） |
| `risk.py` | 発注前の安全弁。**ここを通らない注文は存在しない** |
| `broker.py` | 執行。`PaperBroker`（仮想）と `LiveBroker`（実弾） |
| `state.py` | 残高・建玉・当日損益。JSON で持ち越す |
| `engine.py` | シグナル → 審査 → 執行の 1 サイクル |
| `main.py` | エントリポイント（1 起動 = 1 判断） |
| `backtest.py` | 同じロジックを過去データに当てる |

本番もバックテストも `engine.step()` を通るので、両者の挙動が食い違わない。

## 動かす

```bash
pip install -r bot/requirements.txt

python -m bot.main                    # ドライランで 1 回判断
python -m bot.main --verbose          # 詳細ログ
python -m bot.backtest --limit 1000   # 直近 1000 本でバックテスト
python -m bot.backtest --csv data.csv # 手元の CSV でバックテスト
python -m pytest -q                   # テスト
```

結果は `bot/state/paper_state.json`（残高・建玉）と `bot/state/trades.csv`（約定履歴）に残る。
CSV は損益グラフや確定申告の材料にそのまま使える。

## 設定（`config.yml`）

とくに効くのはリスク側。

| キー | 意味 |
| --- | --- |
| `risk.order_jpy` | 1 回の買いに使う金額 |
| `risk.max_position_btc` | 保有上限。ナンピンで膨らむのを防ぐ |
| `risk.daily_loss_limit_jpy` | 当日の確定損失がこれを超えたら新規買いを停止 |
| `risk.cooldown_minutes` | 連続約定の間隔制限 |
| `risk.sell_all` | 売りシグナルで全量手仕舞い |

手仕舞い（売り）はクールダウンと損失上限の対象外にしてある。
逃げる動きまで止めると含み損を抱えたまま身動きが取れなくなるため。

戦略を足したいときは `strategy.py` の `Strategy` を継承して `STRATEGIES` に登録する。
シグナルは確定足の列だけから決まる純関数なので、そのままバックテストできる。

## GitHub Actions

`.github/workflows/trade.yml` が毎時 5 分（UTC）に起動し、実行後の状態ファイルを
コミットして次回に引き継ぐ。ランナーは毎回まっさらなので、この方式で状態を持ち回る。

注意点：

- **スケジュール実行はデフォルトブランチ（master）にある workflow しか動かない。**
  作業ブランチで試すときは Actions タブから手動実行（workflow_dispatch）する。
- cron は混雑時に数分〜十数分遅れる。分単位の精度が要る戦略には向かない。
- 状態ファイルを毎回コミットするのでコミット履歴は増える（`[skip ci]` 付き）。

## 実弾に切り替える（自己責任）

事故防止のため、鍵が 3 つそろわないと実発注できない。

1. `config.yml` を `mode: { dry_run: false, live_enabled: true }` にする
2. 実行時に `--live` を付ける
3. 環境変数 `CONFIRM_LIVE_TRADING=yes` を渡す

API キーは `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` から読む。
設定ファイルやコードには**絶対に書かない**（このリポジトリは公開されている）。
GitHub Actions では Settings → Secrets and variables → Actions に登録する。

取引所側でキーを発行するときは **取引権限のみ、出金権限なし**、可能なら IP 制限も付ける。

## 承知しておくこと

- 移動平均クロスはレンジ相場で往復ビンタを食らう。まずバックテストで確認すること。
- ドライランの約定価格は「終値 ± スリッページ」の近似で、板の厚みは見ていない。
  実弾では想定より不利に約定する。
- 成行注文しか出さない。指値・逆指値・建玉の分割には未対応。
- 現物のみ（レバレッジ・先物は非対応）。
- 国内取引所の API 利用規約とレートリミットを守ること。
- 暗号資産の売買益は原則として雑所得。`trades.csv` を保存しておくと後で楽。
