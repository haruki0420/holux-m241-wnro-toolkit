# Holux M-241 WNRO 修復ツールキット — Windows / Linux / macOS

[![CI](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS-informational)](#対応プラットフォーム)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

生産終了した **Holux M-241** GPS ロガーを、**Windows・Linux・macOS のどれからでも**
復活させるための Pure Python ツールキットです。

> English README: [`README.en.md`](README.en.md)
> なぜ壊れるのかの詳しい解説: [`docs/ja/00-background.ja.md`](docs/ja/00-background.ja.md)
> 未検証事項を含む検証記録: [`docs/09-verification.md`](docs/09-verification.md)

---

## このリポジトリでできること（全体像）

**一言でいうと:** 日付が 19.6 年ずれるようになった Holux M-241 を、
Windows / Linux / macOS のどの PC からでも、純正の Windows 専用ツールなしで
**診断 → 吸い出し → 修正 → 書き戻し → ログ変換** まで完結させるツールです。

```
  ┌─────────────┐   ① 診断・吸い出し    ┌──────────────┐
  │  Holux      │ ───────────────────▶ │  PC          │
  │  M-241      │   (USB シリアル)      │  Win/Linux/  │
  │  (実機)     │ ◀─────────────────── │  macOS       │
  └─────────────┘   ③ 修正して書き戻し  └──────────────┘
                          ▲                     │
                          │          ② 4 バイト修正
                          └─────────────────────┘
```

### できること（コマンド別）

| やりたいこと | コマンド | 実機が必要？ |
|---|---|---|
| どの USB ポートが M-241 かを調べる | `m241 ports` | 接続のみ |
| 環境・純正パッケージ・デバイスの健康診断 | `m241 doctor` | 任意 |
| ファームのバージョンや機種を確認する | `m241 info` | 必要 |
| **ファームを吸い出す（壊さない・読み取りのみ）** | `m241 dump` | 必要 |
| **WNRO を直すパッチを当てる（4 バイト）** | `m241 patch` | 不要（ファイル操作） |
| **修正したファームを書き戻す** | `m241 flash` | 必要（破壊的） |
| 吸い出したログの中身を調べる | `m241 logs info` | 不要 |
| ログを GPX / CSV / NMEA / KML / JSON に変換 | `m241 logs read` | 不要 |
| 何ポイント記録できるか計算する | `m241 logs capacity` | 不要 |
| 保存済み NMEA の日付を直す | `m241 nmea repair` | 不要 |
| **書き換えずに NMEA をリアルタイム補正** | `m241 nmea proxy` | 必要 |
| 実機なしで全部の動作を自己テスト | `m241 selftest` | 不要 |

### 具体的に解決できる困りごと

1. **「M-241 の日付が 2006 年になる」** → `m241 patch` + `m241 flash` で端末自体を修正
2. **「OLED の時計を直したい」** → 上と同じ（ファームを直すしか方法がない）
3. **「書き換えは怖い。ログだけ正しくしたい」** → `m241 logs read --utc-offset-days 7168`
4. **「アプリで見る日付だけ直したい。端末は触りたくない」** → `m241 nmea proxy`
5. **「Windows しか純正ツールがない。Mac/Linux しか持っていない」** → 本ツールは Pure Python。3 OS すべてで動きます
6. **「V1.12 と V1.13 で記録数が違うらしいが、実際何ポイント入るのか」** → `m241 logs capacity`
7. **「吸い出したログが読めない」** → `m241 logs info` がビットマスクから実測レコード長を判定して解析
8. **「DA バイナリがどれか分からない」** → `m241 doctor --from-drop` が純正パッケージをハッシュ照合して抽出

### 向いていないこと（できないこと）

- ❌ **M-241 以外の機種の修正**（プロトコル層は流用可能ですが、各機種のプロファイルが必要）
- ❌ **文鎮化した個体のソフトウェア復旧**（JTAG がないため、BootROM が応答しなくなったら終わり）
- ❌ **proprietary バイナリの配布**（DA やファームは各自で用意）
- ❌ **GUI 操作**（CLI のみ。それが設計方針）

### 必要なもの

| | |
|---|---|
| ハード | Holux M-241（WNRO バグのある個体）＋ USB ケーブル（CP210x） |
| PC | Windows 10/11、Linux、macOS のいずれか（Python 3.10 以上） |
| ファイル | `DA_v3.8.4.bin`（純正ロガーユーティリティ同梱）＋ 自分で吸い出したダンプ |

---

## 症状をひとことで

GPS は週番号を **10 bit** で放送するため、**1024 週（7168 日 ≒ 19.6 年）**ごとに
必ず一周します。直近の第2ロールオーバーは **2019-04-06** でした。

M-241 のメインファームは 2006 年ビルドで、GPS 時刻 → Unix 時刻の変換に
定数 `315964800`（1980-01-06）をそのまま埋め込んでおり、2 回目のロールオーバーを
想定していません。その結果、現在の日付が **ちょうど 7168 日前**として扱われます。
影響するのは **OLED の時計・NMEA の `$GPRMC`・バイナリログの 3 つすべて**です。

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
                                                    ^^^^^^
                                                    2006-09-17（誤）
                                                    2026-05-03（パッチ後）
```

Holux は 2019 年に GPS 事業から撤退しており、純正の修正ファームは出ません。
自分で直すしかありません。

## ファームを入れたら WNRO は解消するのか

**はい、日付のずれは解消します。ただし「永久に直る」わけではありません。**
正確には次のとおりです。

### 直るもの

| 項目 | 結果 |
|---|---|
| OLED の日付表示 | ✅ 正しくなる（時計そのものは元々正しく、日付だけがずれていた） |
| NMEA `$GPRMC` / `$GPZDA` の日付 | ✅ 正しくなる（地図アプリ・gpsd 等もそのまま正しく見える） |
| 以後に書かれるバイナリログの日時 | ✅ 正しくなる |
| 時刻（hhmmss） | 元から正しい（ずれていたのは日付のみ） |

### 直らないもの

| 項目 | 理由 |
|---|---|
| **パッチ前に書いた既存ログ** | 記録済みのタイムスタンプは書き換わりません。`m241 logs read --utc-offset-days 7168` で後処理してください |
| **2038-11-21 以降（第3ロールオーバー）** | このパッチは「+1024 週（＝1 回分）」ずらすだけです。第3ロールオーバーを越えると、また 1024 週ずれます（次に壊れるのは 2038 年末〜2039 年初） |
| 端末のその他の不具合 | WNRO 以外の症状（電池、ボタン、Bluetooth 等）には無関係です |

### なぜ「1 回分ずらす」で足りるのか

ファーム内の定数は「GPS エポック（1980-01-06）の Unix 時刻」です。
これを **+1024 週した値に置き換える**と、ファームの計算結果が
「2 回目のロールオーバーを考慮した値」になります。

```
315964800  ->  935280000      (+619,315,200 秒 = +1024 週 = +7168 日)
```

第2ロールオーバー（2019）を吸収し、第3ロールオーバー（2038-11-21）までは
正しく動きます。**それ以降も使いたい場合は、同じ要領で再度 +1024 週すれば
理屈上は延命できます**（本ツールは `--wnro-shift-weeks` のような任意シフトには
まだ対応していないため、その場合はプロファイル追加が必要です）。

### 「本当に直るのか」の検証状況

- このオフセット（`0x402F4`）とパッチ内容は、**原著者が実機で検証済み**です
  （[catea.tech の記事](https://catea.tech/blog/holux-m241-wnro-fix)）。
- 本リポジトリは、その結果を **純正ファームに対して決定論的に再現**し、
  出力 SHA256 をテストで固定しています。
- ただし **本リポジトリの構築時に実機への書き込みは行っていません**。

### 実機で確認できたこと・できなかったこと（2026-11 時点）

実際の M-241（V1.13 / `01017-00F` / MT3318、Windows 11 + CP210x で COM3）を
接続して確認した結果です。

| 項目 | 結果 |
|---|---|
| ポート認識・ドライバ | ✅ CP210x を入れて COM3 として認識 |
| `m241 info` でファーム識別 | ✅ `B-core_1.20, 0043, 01017-00F` を取得 |
| WNRO バグの実機再現 | ✅ `$GPRMC,...,010407`（2007-04-01）を観測 |
| `nmea proxy` / `nmea repair` / `logs` / `patch` | ✅ BootROM 不要なので利用可 |
| **`m241 dump`（BootROM 同期）** | ❌ **この個体では同期できず** |

**BootROM に入れないため、dump → patch → flash の実機検証は未完了です。**
RTS/DTR パルス、`$PMTK180`、ボーレート総当たり、連続 `0xA0` ストリーム、
リセットエッジと同時ストリーム（8 通り）をすべて試しましたが、
チップからは 1 バイトも返りませんでした。

次に必要なのは **純正ユーティリティの書き込み手順を USB キャプチャで記録**し、
BootROM に入る直前のシーケンスを特定することです（原著者が使った手法）。
この個体差・経路差の詳細と試した全ルートは
[`docs/10-hardware-session-notes.md`](docs/10-hardware-session-notes.md) に
記録しています。

### 焼かずに済ませたい場合

| 方法 | ログ | ライブ NMEA | OLED |
|---|---|---|---|
| 後処理で +7168 日 | ✅ | ❌ | ❌ |
| `m241 nmea proxy`（ホスト側で補正） | ✅ | ✅ | ❌ |
| 本ツールのファームパッチ | ✅ | ✅ | ✅ |

「持ち歩いて、その場で画面の日付を見たい」という要求だけはファーム改造でしか
満たせません。

## 対応プラットフォーム

| | 状態 |
|---|---|
| **Windows 10/11** | ✅ `COM3` など。CP210x ドライバは純正パッケージから |
| **Linux** | ✅ `/dev/ttyUSB0`。ユーザーを `dialout` グループに追加 |
| **macOS（Intel / Apple Silicon）** | ✅ `/dev/tty.usbserial-*`。最近の macOS はドライバ不要 |
| Python | 3.10 / 3.11 / 3.12 / 3.13 |

すべて Pure Python + `pyserial` です。**コンパイル不要**、`ctypes` 不要、
プラットフォーム固有バイナリなし、GUI なし。

## インストール

```bash
git clone https://github.com/haruki0420/holux-m241-wnro-toolkit.git
cd holux-m241-wnro-toolkit
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"
m241 --help
```

clone せずに入れる場合:

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
```

## クイックスタート（dump → patch → flash）

元記事と同じ 3 ステップです。各段階で SHA256 を表示するので、
「何を書いたか」を後から証明できます。

**この 3 ステップが何をしているか:**

```
① dump   : いま端末に入っているファームを PC にコピー（読み取りだけ・安全）
② patch  : コピーしたファイルの 4 バイトを書き換え（端末は触らない）
③ flash  : 書き換えたファイルを端末に書き戻す（ここだけが破壊的）
```

「② で失敗しても端末は無傷」「③ で失敗すると危険」という構造なので、
① のバックアップが命綱になります。

```bash
# 0) ケーブルを探す（M-241 の電源を入れて USB 接続）
m241 ports

# 1) 自分のファームをダンプ。2 回読んでハッシュ一致を検証します。
#    読み取りのみ。デバイスには一切書き込みません。
m241 dump auto -o backup/m241_stock.bin

# 2) パッチ。イメージから V1.12 / V1.13 を自動判別します。
#    まず --dry-run で「何が変わるか」を確認するのがおすすめ。
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin --dry-run
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin

# 3) 書き戻し。破壊的です。先に DISCLAIMER.md を読んでください。
m241 flash auto \
    --da vendor/DA_v3.8.4.bin \
    --fw backup/m241_v114_wnro.bin \
    --backup backup/m241_stock.bin
```

電源を入れ直して OLED を確認してください。**V1.14** と表示され、
`$GPRMC` の日付が今日になっていれば成功です。

### 失敗したときの戻し方

`m241 flash` の途中で失敗した場合は、**同じコマンドの `--fw` を元のダンプに
差し替えるだけ**で元に戻せます。

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin \
    --fw backup/m241_stock.bin \
    --backup backup/m241_stock.bin --yes
```

これが「ダンプは 2 回取ってハッシュを比べる」ことが必須な理由です。

### `DA_v3.8.4.bin` とファームはどこから？

いずれも **proprietary なので同梱していません**。お手持ちの純正アップデート
パッケージから取り出します。ツールがハッシュ検証つきで抽出します。

```bash
# ../holux/ にある純正 zip 群から、必要なファイルだけを取り出す
m241 doctor --from-drop ../holux --extract-to vendor/
```

このコマンドは抽出した全ファイルの SHA256 を照合し、どのファーム
バージョンを持っているかを表示して、`vendor/DA_v3.8.4.bin` を書き出します
（`--da` で純正インストーラ内のファイルを直接指しても構いません）。

純正パッケージを持っていなくても、**自分の端末からダンプすればそれがファーム
そのもの**です。必要なのは DA バイナリだけで、それは
`HoluxLoggerUtility.exe` と同じディレクトリにあります。

## やりたいこと別ガイド

どのコマンドを使えばいいか迷ったら、ここから探してください。

### A. 端末自体を直したい（OLED の日付も直す）

```bash
m241 ports                                          # ポート確認
m241 info auto                                      # ファーム識別
m241 dump auto -o backup/stock.bin                  # ① 吸い出し
m241 patch backup/stock.bin -o backup/v114.bin      # ② 4 バイト修正
m241 flash auto --da vendor/DA_v3.8.4.bin \
    --fw backup/v114.bin --backup backup/stock.bin  # ③ 書き戻し
```

所要時間の目安: 診断 1 分 / ダンプ 1〜2 分（2 回） / パッチ 1 秒 / 書き込み 1〜2 分。

### B. 端末は触らず、ログの日付だけ直したい

```bash
m241 logs info data.bin                                        # まず中身を確認
m241 logs read data.bin --format gpx \
    --utc-offset-days 7168 -o fixed.gpx                        # GPX で出力
m241 logs read data.bin --format csv --utc-offset-days 7168 -o fixed.csv
```

`--utc-offset-days auto` にすると、記録が古い場合だけ自動で補正します。

### C. 地図アプリで見る日付だけ直したい（端末は触らない）

```bash
m241 nmea proxy auto --offset-days 7168 --tcp 127.0.0.1:10110
# → 地図アプリの GPS 入力を 127.0.0.1:10110 に設定
```

OLED の時計は狂ったままですが、アプリ側は正しい日付になります。

### D. まず何も壊さずに動作確認したい

```bash
m241 selftest        # 実機なしで全機能を自己テスト
m241 doctor          # 環境と接続の健康診断
```

`m241 selftest` は実機を一切使わず、同梱のシミュレータ上で
dump → patch → flash → 再読込まで完走します。インストールが正しいかの確認に
最適です。

### E. このファームが何ポイント記録できるか知りたい

```bash
m241 logs capacity                  # 2M/4M × 16B/20B の表
m241 logs info mydump.bin           # 実測のレコード長を表示
```

## コマンド一覧

```
m241 ports                       シリアルポート一覧（M-241 候補を強調）
m241 doctor                      環境・純正パッケージ・デバイスの健康診断
m241 info <port>                 PMTK605/705 でファームを識別
m241 dump <port> -o FILE         BootROM 経由でファームを吸い出す（読み取りのみ・安全）
m241 patch SRC -o DST            WNRO パッチ＋バージョン表記の変更
m241 flash <port> --da --fw      DA 経由で書き戻す（破壊的）
m241 logs info FILE              吸い出したログのブロック／レコード情報
m241 logs read FILE --format gpx ログを GPX / CSV / NMEA / KML / JSON に変換
m241 logs capacity               2M/4M × 16B/20B の記録可能数テーブル
m241 nmea repair                 NMEA ファイルの日付補正
m241 nmea proxy <port>           NMEA をリアルタイム補正して TCP 配信
m241 selftest                    シミュレータによるエンドツーエンド自己テスト
```

すべてのコマンドが `-h` に対応しています。ハードウェア系コマンドは
`--json` で機械可読な結果を返せます。終了コードは
`0` 成功 / `1` 実行時エラー / `2` 引数エラー / `3` デバイス未検出 /
`4` 検証失敗 / `5` 安全装置による拒否 / `6` 書き込み失敗 です。

## ファーム V1.12 と V1.13 で記録できる数が違う理由

どちらのバージョンもカタログ上は「130,000 ポイント」ですが、これは
**16 バイト記録のときだけ**到達できる数字です。純正 V1.13 のリリースノートに
こうあります。

> `2. log format 16 Byte change to 20 Byte. new Speed format.`

増えた 4 バイトは speed（little-endian float）です。したがって実際の上限は:

| フラッシュ | ブロック数 | 実効バイト数 | 16 B 記録（〜V1.12） | 20 B 記録（V1.13） |
|---|---|---|---|---|
| 2 MiB | 32 × 64 KiB | 2,080,768 | **130,048** | **104,038** |
| 4 MiB | 64 × 64 KiB | 4,161,536 | 260,096 | 208,076 |

（各 64 KiB ブロックの先頭 `0x200` バイトはヘッダなので、実効は 1 ブロック
`65024` バイトです。）

既定の 1 Hz 記録だと:

| 形式 | 2 MiB | 4 MiB |
|---|---|---|
| 16 B | 約 36.1 時間 | 約 72.2 時間 |
| 20 B | 約 28.9 時間 | 約 57.8 時間 |

`m241 logs capacity` がこの表を出し、`m241 logs info` はダンプに実際に
書かれているビットマスクから **実測のレコード長** を報告します。
「12 と 13 で記録数が違う」問題は推測せず、イメージから機械的に判定する設計です。

## パッチの中身

```
0x402F4 : 0x12D53D80 -> 0x37BF3D80
          315964800     935280000   （+619,315,200 秒 = +1024 週 = +7168 日）
0x59B14 : "V1.12" -> "V1.14"        （改造済みの目印・任意）
0x59C28 : "V1.13" -> "V1.14"        （改造済みの目印・任意）
```

エポック定数はイメージ内に **2 か所**ありますが、時刻計算に効くのは
`0x402F4` の 1 か所だけです。両方書き換えると 2045 年まで飛びます
（原著者が実機で実証）。本ツールは 1 か所だけ書き換え、
`tests/test_firmware_patcher.py` でその挙動を固定しています。

検証済みの出力ハッシュ:

| 入力 | 出力 |
|---|---|
| V1.12 `4103601a…` | `dad2983d95cc3be251b358b2b7d5da9272d9946e70aaa0067162a4c21475c384` |
| V1.13 `32fe5269…` | `901555843895054f77716364a446e8ad41c00b799c781b4ff95144352c141c9a` |

導出の全記録: [`docs/04-firmware-profiles.md`](docs/04-firmware-profiles.md)

## 書き換えずに済ませる方法

```bash
# 吸い出したログの日付を補正
m241 logs read data.bin --format gpx --utc-offset-days 7168 -o fixed.gpx

# リアルタイム補正（地図アプリの入力を 127.0.0.1:10110 に向ける）
m241 nmea proxy auto --offset-days 7168 --tcp 127.0.0.1:10110
```

OLED の時計は狂ったままですが、アプリ側では正しい日付が見えます。

## 注意（必読）

JTAG ヘッダのない機器の NOR フラッシュを書き換えます。**失敗すると文鎮化します。**
書き込み前に必ず [`DISCLAIMER.md`](DISCLAIMER.md) を読んでください。要点:

1. ダンプは 2 回取って SHA256 を比較し、バックアップを保管する。
2. **自分で吸い出したイメージから作ったパッチだけ**を書き込む。
3. 書き込み中にスリープ・給電断・ケーブル抜去をしない。

## リポジトリ構成

```
src/m241/
  protocol/     MTK BootROM + Download Agent、シリアルポート探索、シミュレータ
  firmware/     イメージ解析、バージョンプロファイル、パッチエンジン
  logfile/      MTK/Holux バイナリログ解析、容量計算、エクスポータ
  nmea/         NMEA センテンス解析、WNRO 補正、TCP プロキシ
  data/         同梱のファームプロファイル JSON（ハッシュとオフセットのみ）
  cli.py        `m241` コマンド本体
scripts/        元記事の dump/patch/flash 手順をそのまま使える互換シム
docs/           背景・プロトコル・ログ形式・プラットフォーム・検証記録
tests/          合成イメージと擬似デバイスだけで完結するテスト群
tools/          解析・検証用スクリプト（実機不要）
```

### どのファイルを見ればいいか

| 知りたいこと | 見るファイル |
|---|---|
| まず使ってみたい | この README の「クイックスタート」「やりたいこと別ガイド」 |
| なぜ壊れるのか知りたい | [`docs/ja/00-background.ja.md`](docs/ja/00-background.ja.md) |
| 何がどれだけ検証済みか | [`docs/09-verification.md`](docs/09-verification.md) |
| 自分でファームを解析したい | [`tools/re/analyze_firmware.py`](tools/re/analyze_firmware.py) |
| エラーが出た | [`docs/06-troubleshooting.md`](docs/06-troubleshooting.md) → [`docs/08-quick-card.md`](docs/08-quick-card.md) |
| 危険性を知りたい | [`DISCLAIMER.md`](DISCLAIMER.md) |
| プロトコルを実装したい | [`docs/01-protocol.md`](docs/01-protocol.md) |
| ログ形式を解析したい | [`docs/02-log-format.md`](docs/02-log-format.md) |
| V1.12/V1.13 のオフセット根拠 | [`docs/04-firmware-profiles.md`](docs/04-firmware-profiles.md) |
| 貢献したい | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## コントリビュート

特に歓迎するのは次の 3 つです。

- **V1.11 など他のファーム**のプロファイル追加
  （何が必要かは [`docs/04-firmware-profiles.md`](docs/04-firmware-profiles.md)）
- **4 MiB 機・GR-245 での動作確認**
- **Windows / macOS での実機レポート**

PR を出す前に `pytest` と `ruff check .` を実行してください。
CI が 3 OS すべてで両方を回します。

## クレジット

- 元になった逆解析、BootROM/DA プロトコルの解読、そして
  `DA_SetStartAddr(handle, 0x100800)` という魔法の数字の発見:
  [猫葉Tech / Catea Tech](https://catea.tech/blog/holux-m241-wnro-fix) と
  そのプロジェクト
  [NekobaTechnology/holux-m241-wnro-fix](https://github.com/NekobaTechnology/holux-m241-wnro-fix)（MIT）。
  本プロジェクトは、その知見を出発点に **マルチプラットフォーム対応・
  テスト可能なシミュレータ・V1.12/V1.13 両対応** を加えた独立実装です。
- [`dimhoff/mt333x-fw-utils`](https://github.com/dimhoff/mt333x-fw-utils)
  — Python 版 MTK プロトコルの原典。
- [GPSBabel](https://www.gpsbabel.org/)（`mtk_logger.cc`）
  — MTK/Holux ログ形式の一次資料。M-241 のクセ（4 バイト float 緯度経度、
  3 バイト高度、1 バイト CRC）もここに書かれています。
- 動かなくなった機器を動くままにしているすべての人へ。

## ライセンス

コードとドキュメントは [MIT](LICENSE)。proprietary なバイナリと
ファームウェアイメージは各権利者のもので、本リポジトリには含みません。
