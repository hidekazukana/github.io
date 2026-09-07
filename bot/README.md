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
| `doctor.py` | 発注前の設定チェック（通信なし・注文なし） |
| `backtest.py` | 同じロジックを過去データに当てる |

本番もバックテストも `engine.step()` を通るので、両者の挙動が食い違わない。

## 動かす

```bash
pip install -r bot/requirements.txt

python -m bot.doctor                  # 設定の点検（まずこれ）
python -m bot.main                    # ドライランで 1 回判断
python -m bot.main --verbose          # 詳細ログ
python -m bot.backtest --limit 1000   # 直近 1000 本でバックテスト
python -m bot.backtest --csv data.csv # 手元の CSV でバックテスト
python -m pytest -q                   # テスト
```

結果は `bot/state/paper_state.json`（残高・建玉）と `bot/state/trades.csv`（約定履歴）に残る。
CSV は損益グラフや確定申告の材料にそのまま使える。

## 取引所の選び方

ccxt で見た国内取引所の対応状況（`python -m bot.doctor` で同じ判定ができる）。

| 取引所 | 発注 | ローソク足 | 設定 |
| --- | --- | --- | --- |
| bitbank | OK | OK | `id: bitbank` のままでよい |
| bitFlyer | OK | **なし** | `id: bitflyer` + `ohlcv_exchange_id: bitbank` |
| Coincheck | OK | **なし** | `id: coincheck` + `ohlcv_exchange_id: bitbank` |
| btcbox / Zaif | OK | **なし** | 同上 |
| GMOコイン | ccxt 非対応 | — | このボットからは発注できない |

足を別の取引所から取ると価格が完全には一致しない。判断が数千円ずれることがあるので、
シグナルがシビアな戦略では bitbank に寄せるのが無難。

## 設定（`config.yml`）

とくに効くのはリスク側。

| キー | 意味 |
| --- | --- |
| `risk.order_jpy` | 1 回の買いに使う金額 |
| `risk.min_order_jpy` | これを下回る注文は出さない |
| `risk.min_order_btc` | 取引所の最小注文数量（bitbank の BTC/JPY は 0.0001 BTC） |
| `risk.max_position_btc` | 保有上限。ナンピンで膨らむのを防ぐ |
| `risk.fee_buffer_rate` | 残高いっぱいに買うときに残す余裕（手数料＋滑り） |
| `risk.daily_loss_limit_jpy` | 当日の確定損失がこれを超えたら新規買いを停止 |
| `risk.cooldown_minutes` | 連続約定の間隔制限 |
| `risk.sell_all` | 売りシグナルで全量手仕舞い |

手仕舞い（売り）はクールダウンと損失上限の対象外にしてある。
逃げる動きまで止めると含み損を抱えたまま身動きが取れなくなるため。

注文サイズは円建てと数量の両方で下限を見ている。円建てだけだと、BTC 価格が上がったときに
数量が取引所の最小単位を割り、注文が弾かれ続ける。`python -m bot.doctor` が
「BTC が◯円を超えると発注できなくなる」上限価格を教えてくれる。

### 少額で動かすとき（例: 1 万円）

既定値は 100 万円を前提にしている。資金が少ないときは必ず 3 つを下げること。

| キー | 既定 | 1 万円なら | 理由 |
| --- | --- | --- | --- |
| `order_jpy` | 10000 | 2000〜3000 | 既定のままだと 1 回の注文に全資産が乗る（全額ベット） |
| `daily_loss_limit_jpy` | 5000 | 500〜1000 | 資金の 50% を失ってからでは安全弁の意味がない |
| `max_position_btc` | 0.01 | 0.001 | 0.01 BTC は 1 万円では買えない量（上限として効かない） |

`order_jpy` を下げると、BTC 価格が上がったときに最小注文数量 0.0001 BTC を割る価格が
下がってくる。`python -m bot.doctor` がその上限価格を表示するので確認すること
（例: `order_jpy: 2000` なら BTC が 2,000 万円を超えた時点で発注できなくなる）。

手数料は率なので資金が小さくても不利にはならないが、利益の絶対額は小さくなる。
1 回 2,000 円の注文で相場が 2% 動いても、手取りは 30 円ほど。

戦略を足したいときは `strategy.py` の `Strategy` を継承して `STRATEGIES` に登録する。
シグナルは確定足の列だけから決まる純関数なので、そのままバックテストできる。

## GitHub Actions

`.github/workflows/trade.yml` が毎時 5 分（UTC）に起動し、実行後の状態ファイルを
コミットして次回に引き継ぐ。ランナーは毎回まっさらなので、この方式で状態を持ち回る。

注意点：

- **スケジュール実行はデフォルトブランチ（master）にある workflow しか動かない。**
  作業ブランチで試すときは Actions タブから手動実行（workflow_dispatch）する。
  逆にいえば、master にマージした時点で毎時の自動実行が始まる。
- cron は混雑時に数分〜十数分遅れる。分単位の精度が要る戦略には向かない。
- 状態ファイルを毎回コミットするのでコミット履歴は増える（`[skip ci]` 付き）。
- **このリポジトリは GitHub Pages のユーザーサイト**なので、master へのコミットごとに
  サイトが再ビルドされる。`[skip ci]` が抑えるのは Actions のワークフローだけで、
  Pages の再ビルドは止まらない。動作に害はないがデプロイ履歴は毎時増える。
- GitHub は一定期間リポジトリに活動がないとスケジュール実行を自動で無効化する。
  長く放置するときは Actions タブで動いているか時々確認すること。
- 状態ファイルの push が人手のコミットと衝突した場合は、取り込んで 3 回まで押し直す。
  それでも失敗したらジョブを失敗させる（黙って残高を失わないため）。

### 止め方

- 一時的に止める … Actions タブ → 該当 workflow → 右上の `...` → Disable workflow
- 恒久的に止める … `trade.yml` の `schedule:` を消す（`workflow_dispatch` だけ残す）

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
