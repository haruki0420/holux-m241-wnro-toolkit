# Holux M-241 の日付が 2007 年になる問題を直す

M-241 が記録する日付が **19.6 年ずれている**（2026 年なのに 2007 年になる）なら、
このツールで直せます。Windows / Mac / Linux のどれでも動きます。

原因は GPS の週数カウンタが 2019 年 4 月に一周したことです。M-241 のファームは
2006 年製で、その周回を考慮していません。詳しくは
[症状と原因](docs/user/01-symptoms.md)。

---

## 直せます

| 直るもの | 状態 |
|---|---|
| NMEA の日付（地図アプリ・gpsd が見る値） | ✅ 実機で確認済み |
| 以後に記録するログの日時 | ✅ NMEA と同じ計算経路 |
| OLED の日付表示 | ⚠️ 同じ経路なので直るはずだが画面は未確認 |

| 直らないもの | 理由 |
|---|---|
| パッチ前に記録した既存ログ | 記録済みの値は変わりません。後処理で補正できます |
| 2038 年 11 月以降 | この修正は「1 回分」ずらすだけです。また一周すると再発します |
| 日付以外の不具合（電池・ボタン・Bluetooth） | WNRO とは無関係です |

---

## 何をインストールするのか

**`m241` コマンド 1 つだけ**です。Python のパッケージとして入ります。

```bash
pip install "git+https://github.com/haruki0420/holux-m241-wnro-toolkit.git"
m241 --help
```

これで終わりです。デーモンも GUI も常駐ソフトもありません。
アンインストールは `pip uninstall m241-wnro-toolkit`。

> **Python 3.10 以上が必要です。** 依存は `pyserial` のみで、それ以外は標準ライブラリです。
> リポジトリを clone する必要はありません。

---

## 必要なもの

| 要るもの | 補足 |
|---|---|
| M-241 本体 | 電源が入り、USB で PC につながること |
| USB ケーブル | 付属のもの |
| PC | Windows 10/11（実機検証済み）、Linux / macOS（未検証） |
| 純正ツールから取り出すファイル 1 つ | **ファーム書き換え時のみ**。下記参照 |

### 書き換えにだけ必要なファイル

ファームの書き換えには `DA_v3.8.4.bin` という小さなプログラムが要ります。
これは純正の `HoluxLoggerUtility` に同梱されていたもので、**このリポジトリには
含めていません**（権利上、再配布できないため）。

Holux は 2019 年に GPS 事業から撤退しており公式配布は終了していますが、
第三者サイトで入手できます。入手したら、このツールで中身を検証できます。

```bash
m241 doctor --from-drop <入手したファイルのあるフォルダ> --extract-to vendor/
```

**書き換えをしない場合、このファイルは不要です。** ログの取り出し・変換・
地図アプリ用の日付補正は、これ無しで全部動きます。

---

## 手順

### 1. まず何も壊さずに確認する

```bash
m241 doctor          # 環境とデバイスの診断
m241 info            # つながっている M-241 のファームを表示
```

`m241 info` が `B-core_1.20, 0043, 01017-00F` のような文字列を返せば、
PC と M-241 は正しくつながっています。

### 2. バックアップを取る（読み取りのみ・安全）

```bash
m241 dump auto -o backup/stock.bin
```

ファーム全体（約 400 KB）を PC に保存します。**デバイスには一切書き込みません。**
2 回読んでハッシュが一致することを自動で確認します。

### 3. 修正する（PC 内で完結）

```bash
m241 patch backup/stock.bin -o backup/fixed.bin --dry-run   # 何が変わるか確認
m241 patch backup/stock.bin -o backup/fixed.bin             # 実行
```

変更されるのは **4 バイトだけ**です。

### 4. 書き戻す（ここだけ危険）

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin \
    --fw backup/fixed.bin --backup backup/stock.bin
```

書き込み後に読み戻してハッシュを照合します。一致しなければ失敗として報告します。

### 5. 確認する

```bash
m241 info
```

NMEA の日付が今日になっていれば成功です。地図アプリで確認する場合は
`m241 nmea proxy` を使ってください。

---

## 失敗したとき

**BootROM はチップ内蔵の消せない領域なので、書き込みに失敗しても本体は壊れません。**
電源を入れ直せば元の状態で起動します。

```bash
# 1. 電池を抜いて 10 秒待ち、入れ直す
# 2. もう一度書き込む
m241 flash auto --da vendor/DA_v3.8.4.bin \
    --fw backup/stock.bin --backup backup/stock.bin
```

詳しくは [失敗したとき](docs/user/06-troubleshooting.md)。

---

## 書き換えをしない選択肢

端末の日付は直りませんが、実害を減らす方法があります。

| 方法 | ログの日付 | 地図アプリの日付 | OLED |
|---|---|---|---|
| 吸い出したログを後処理で補正 | ✅ | — | ❌ |
| リアルタイムで日付を補正して中継 | ✅ | ✅ | ❌ |
| ファームを書き換える | ✅ | ✅ | ⚠️ |

```bash
# ログを後処理で補正
m241 logs read backup/track.bin --format gpx -o track.gpx

# 地図アプリに渡す日付をリアルタイム補正
m241 nmea proxy auto --tcp 127.0.0.1:10110
```

---

## そのほかにできること

| やりたいこと | コマンド | ドキュメント |
|---|---|---|
| ログを取り出す | `m241 logs download` | [ログ](docs/user/05-photos.md) |
| 写真に位置情報を付ける | `m241 logs geotag` | [写真](docs/user/05-photos.md) |
| ログを GPX / KML / CSV に変換 | `m241 logs read` | [ログ](docs/user/05-photos.md) |
| 端末のログを消す | `m241 logs erase` | [ログ](docs/user/05-photos.md) |
| 地図アプリ用に日付を補正 | `m241 nmea proxy` | [地図アプリ](docs/user/01-symptoms.md) |
| 記録容量を知る | `m241 logs capacity` | [機能一覧](docs/reference/05-features.md) |

コマンドの全一覧は [`m241 --help`](docs/reference/04-cli.md)。

---

## 対応プラットフォーム

| | 状態 |
|---|---|
| **Windows 10/11** | ✅ **実機で検証済み**。CP210x ドライバが必要 |
| **Linux** | ⚠️ **実機未検証**。`/dev/ttyUSB0`、`dialout` グループに追加 |
| **macOS** | ⚠️ **実機未検証**。`/dev/tty.usbserial-*` |
| Python | 3.10 / 3.11 / 3.12 / 3.13（CI で全組み合わせをテスト） |

Windows 以外はコードが動くことしか確認できていません。実機接続の報告を歓迎します。

---

## リポジトリの中身

**使うだけなら `m241` コマンド以外は見る必要ありません。**

```
README.md          ← このファイル
docs/
  user/            利用者向け（症状・インストール・手順・トラブル）
  reference/       仕様（プロトコル・ログ形式・CLI）
  dev/             開発者向け（設計・検証記録・今後の予定）
src/m241/          pip install される中身。利用者は触らない
tests/             自動テスト。利用者は触らない
tools/             開発・検証用。利用者は触らない
AGENTS.md          コードを変更する人向けの注意点
DISCLAIMER.md      書き換え前の注意事項
```

---

## 注意

ファームの書き換えは**自己責任**です。手順を守り、必ずバックアップを取ってから
実行してください。詳細は [DISCLAIMER.md](DISCLAIMER.md)。

## ライセンス

MIT。詳細は [LICENSE](LICENSE)。

## クレジット

WNRO の原因特定とパッチ位置の解明は
[猫葉Tech / Catea Tech](https://catea.tech/blog/holux-m241-wnro-fix) と
[NekobaTechnology/holux-m241-wnro-fix](https://github.com/NekobaTechnology/holux-m241-wnro-fix)（MIT）
によるものです。本リポジトリはその知見を基に、書き込み経路の修正・
ログ取得・写真ジオタグ・クロスプラットフォーム対応を追加しています。
