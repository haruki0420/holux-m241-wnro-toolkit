# Documentation

Three groups, by who is reading:

| Group | For | Contents |
|---|---|---|
| [`user/`](user/) | 使う人 | 症状・準備・手順・失敗したとき・写真・トラブル |
| [`reference/`](reference/) | 仕様を知りたい人 | プロトコル・ログ形式・オフセット・CLI・機能 |
| [`dev/`](dev/) | コードを変える人 | 設計・検証記録・実機メモ・予定 |

**使うだけなら `user/` だけ読めば十分です。**

---

## user/ — 使う人向け

| # | 文書 | 答えること |
|---|---|---|
| 01 | [症状と原因](user/01-symptoms.md) | なぜ日付が 19.6 年ずれるのか、直るのか、直らないのか |
| 01 | [症状と原因（日本語詳解）](user/01-symptoms.ja.md) | 上記の詳細版 |
| 02 | [インストール](user/02-install.md) | 何を入れるか、必要なファイルの入手方法、OS 別の準備 |
| 03 | [ファームを書き換える](user/03-fix-firmware.md) | **実際の手順**（バックアップ → パッチ → 書き込み → 確認） |
| 04 | [失敗したとき](user/04-recovery.md) | 書き込みに失敗して起動しないときの復旧 |
| 05 | [写真に位置情報を付ける](user/05-photos.md) | ログの吸い出しと写真のジオタグ付け |
| 06 | [トラブルシューティング](user/06-troubleshooting.md) | 症状別の対処 |
| 07 | [早見表](user/07-quick-card.md) | 印刷して使う 1 ページ |

## reference/ — 仕様

| # | 文書 | 内容 |
|---|---|---|
| 01 | [プロトコル](reference/01-protocol.md) | BootROM と書き込み用プログラムの通信仕様 |
| 02 | [ログ形式](reference/02-log-format.md) | バイナリログの構造、記録できる点数 |
| 03 | [ファームのオフセット](reference/03-firmware-offsets.md) | 検証済みのアドレス・ハッシュ・サイズ |
| 04 | [CLI 仕様](reference/04-cli.md) | コマンド一覧、終了コード、JSON 出力 |
| 05 | [機能一覧](reference/05-features.md) | 2026 年に何が使えて何が使えないか |
| 06 | [純正パッケージ](reference/06-vendor-drop.md) | Holux の配布物の中身と、このツールが使うもの |

## dev/ — コードを変える人向け

| # | 文書 | 内容 |
|---|---|---|
| 01 | [設計](dev/01-architecture.md) | モジュール構成とインターフェース契約 |
| 02 | [検証記録](dev/02-verification.md) | 何をどう確認したか（未検証も明記） |
| 03 | [実機メモ](dev/03-hardware-notes.md) | 実機作業の時系列記録 |
| 04 | [今後の予定](dev/04-roadmap.md) | 済んだこと・未確認のこと・対象外 |
| 05 | [Holux アプリ再実装計画](dev/05-holux-app-plan.md) | ezTour 等の機能をどう作るか |
| 06 | [プロトコル実装メモ](dev/06-protocol-notes.md) | 実装上の解釈と判断 |

---

## その他のファイル

| ファイル | 内容 |
|---|---|
| [`../README.md`](../README.md) | メインの入口（日本語） |
| [`../README.en.md`](../README.en.md) | メインの入口（英語） |
| [`../AGENTS.md`](../AGENTS.md) | コードを変更する人向けの注意点 |
| [`../DISCLAIMER.md`](../DISCLAIMER.md) | **書き換える前に必ず読む** |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | プロファイル追加・実機報告の方法 |
| [`../SECURITY.md`](../SECURITY.md) | 脆弱性の報告先 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 変更履歴 |
| [`../tests/fixtures/README.md`](../tests/fixtures/README.md) | バイナリのテストデータを置かない理由 |

---

## 30 秒でわかる話

GPS は週番号を 10 ビットで放送しているので、1024 週（19.6 年）ごとに一周します。
最後の一周は 2019-04-06 でした。M-241 の 2006 年製ファームは、GPS の基準時刻
`315964800`（1980-01-06）を直書きしていて、2 回目の一周を考慮していません。
そのため 2026 年の測位が 2007 年として記録されます。

Holux は 2019 年に GPS 事業から撤退したので、公式修正は出ません。直す方法は
自分でファームを書き換えることだけです。変更は 4 バイト:
`0x402F4` の `315964800` を `935280000` に置き換える。
