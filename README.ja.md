# Holux M-241 WNRO 修復ツールキット（Windows / Linux / macOS）

[![CI](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/haruki0420/holux-m241-wnro-toolkit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

生産終了した **Holux M-241** GPS ロガーを、**Windows・Linux・macOS のどれからでも**
復活させるための Pure Python ツールキットです。

- **GPS 週数ロールオーバー（WNRO）バグを 4 バイトのファーム書き換えで修復**
  （2019-04-06 の第2ロールオーバー以降、日付が 7168 日 = 1024 週ずれる問題）
- **MT3318 の BootROM / Download Agent を Python で直接制御** —
  `PowerFlash.exe` も `brom.dll` も Windows も不要
- ファームの **ダンプ → パッチ → 書き戻し** を SHA256 検証つきで実施
- **バイナリトラックログの吸い出しと変換**（GPX / CSV / NMEA / KML / JSON）
- **ファーム V1.12（16 バイト記録）と V1.13（20 バイト記録）の記録数差に対応**
- 書き換えずに済ませたい人向けの **NMEA プロキシ**（リアルタイム補正）
- 実機なしで全経路をテストできる **シミュレータ** 同梱

> English README: [`README.md`](README.md)
> なぜ壊れるのかの解説: [`docs/ja/00-background.ja.md`](docs/ja/00-background.ja.md)

---

## 症状

GPS の週数は 10 bit しかないため 1024 週（7168 日 ≒ 19.6 年）ごとに必ず
オーバーフローします。直近では **2019-04-06** が第2ロールオーバーでした。
M-241 のメインファームは 2006 年ビルドで、Unix エポック定数
`315964800`（1980-01-06）をそのまま使っており、2 回目のロールオーバーを
考慮していません。その結果、現在の日付が **ちょうど 7168 日前** として
記録されます（OLED・NMEA・ログファイルのすべて）。

```
$GPRMC,022127.542,V,8960.0000,N,00000.0000,E,0.00,0.00,170906,,,N*7F
                                                    ^^^^^^
                                                    2006-09-17（誤）
                                                    2026-05-03（パッチ後）
```

Holux は 2019 年に GPS 事業から撤退しており、純正の修正ファームは出ません。
自分で直すしかありません。

## 対応プラットフォーム

| | 状態 |
|---|---|
| **Windows 10/11** | ✅ `COM3` など。CP210x ドライバは同梱の純正パッケージから |
| **Linux** | ✅ `/dev/ttyUSB0`。ユーザーを `dialout` グループに追加 |
| **macOS（Intel / Apple Silicon）** | ✅ `/dev/tty.usbserial-*`。最近の macOS はドライバ不要 |
| Python | 3.10 / 3.11 / 3.12 / 3.13 |

すべて Pure Python + `pyserial` です。コンパイル不要、`ctypes` 不要、
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

## クイックスタート（dump → patch → flash）

```bash
# 0) ケーブルを探す（M-241 の電源を入れて USB 接続）
m241 ports

# 1) 自分のファームをダンプ（読み取りのみ・安全）。2 回読んでハッシュ一致を検証
m241 dump auto -o backup/m241_stock.bin

# 2) パッチ（V1.12 / V1.13 を自動判別）
m241 patch backup/m241_stock.bin -o backup/m241_v114_wnro.bin

# 3) 書き戻し（破壊的。DISCLAIMER.md を必ず読むこと）
m241 flash auto \
    --da vendor/DA_v3.8.4.bin \
    --fw backup/m241_v114_wnro.bin \
    --backup backup/m241_stock.bin
```

電源を入れ直すと OLED の Setting メニューが **V1.14** になり、
`$GPRMC` の日付が今日になります。

### `DA_v3.8.4.bin` はどこから？

proprietary なので同梱していません。お手持ちの純正アップデートパッケージから
取り出します。ツールがハッシュ検証つきで抽出します:

```bash
m241 doctor --from-drop ../holux --extract-to vendor/
```

## コマンド一覧

```
m241 ports                シリアルポート一覧（M-241 候補を強調）
m241 doctor               環境・純正パッケージ・デバイスの健康診断
m241 info <port>          PMTK605/705 でファーム識別
m241 dump <port> -o FILE  BootROM 経由でファームを吸い出す（読み取りのみ）
m241 patch SRC -o DST     WNRO パッチ＋バージョン表記の変更
m241 flash <port> ...     DA 経由で書き戻す（破壊的）
m241 logs info FILE       ログのブロック／レコード情報
m241 logs read FILE       ログを GPX / CSV / NMEA / KML / JSON に変換
m241 logs capacity        2M/4M × 16B/20B の記録可能数テーブル
m241 nmea repair          NMEA ファイルの日付補正
m241 nmea proxy <port>    NMEA をリアルタイム補正して TCP 配信
m241 selftest             シミュレータによるエンドツーエンド自己テスト
```

終了コード: `0` 成功 / `1` 実行時エラー / `2` 引数エラー /
`3` デバイス未検出 / `4` 検証失敗 / `5` 安全装置による拒否。

## ファーム V1.12 と V1.13 で記録できる数が違う理由

どちらのバージョンもカタログ上は「130,000 ポイント」ですが、これは
**16 バイト記録のときだけ** 到達できる数字です。純正 V1.13 の
リリースノートにこうあります:

> `2. log format 16 Byte change to 20 Byte. new Speed format.`

増えた 4 バイトは speed（float）です。したがって実際の上限は:

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
「12 と 13 で記録数が違う」問題は、推測ではなくイメージから機械的に
判定する設計です。

## パッチの中身

```
0x402F4 : 0x12D53D80 -> 0x37BF3D80
          315964800     935280000   （+619315200 秒 = +1024 週 = +7168 日）
0x59B14 : "V1.12" -> "V1.14"        （改造済みの目印・任意）
0x59C28 : "V1.13" -> "V1.14"        （改造済みの目印・任意）
```

エポック定数はイメージ内に 2 か所ありますが、時刻計算に効くのは
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

## 注意

JTAG ヘッダのない機器のフラッシュを書き換えます。失敗すると文鎮化します。
書き込み前に必ず [`DISCLAIMER.md`](DISCLAIMER.md) を読んでください。要点:

1. ダンプは 2 回取って SHA256 を比較し、バックアップを保管する。
2. **自分で吸い出したイメージから作ったパッチだけ**を書き込む。
3. 書き込み中にスリープ・給電断・ケーブル抜去をしない。

## ライセンス

コードとドキュメントは [MIT](LICENSE)。proprietary なバイナリと
ファームウェアイメージは各権利者のもので、本リポジトリには含みません。

## クレジット

- 元になった解析記事とプロジェクト:
  [猫葉Tech「Holux M-241 の GPS 週数ロールオーバーを Mac から 2 バイトで直した」](https://catea.tech/blog/holux-m241-wnro-fix)、
  [NekobaTechnology/holux-m241-wnro-fix](https://github.com/NekobaTechnology/holux-m241-wnro-fix) (MIT)。
  本プロジェクトはその知見を出発点に、マルチプラットフォーム対応・
  テスト可能なシミュレータ・V1.12/V1.13 両対応を加えた独立実装です。
- [`dimhoff/mt333x-fw-utils`](https://github.com/dimhoff/mt333x-fw-utils)
- [GPSBabel](https://www.gpsbabel.org/)（MTK/Holux ログ形式の一次資料）
