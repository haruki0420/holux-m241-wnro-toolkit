# ファームを書き換えて日付を直す

このページは実際の手順です。所要時間の目安は **15〜20分**、うち書き込みは **2〜3分**です
（⚠️ 未検証: 実測記録はありません）。

> **先に読んでください:** BootROM はマスク ROM に入っていて消せないので、書き込みに失敗しても
> 電源を入れ直して自分のバックアップを書き戻せば、たいていは復旧できます
> （[失敗したとき](04-recovery.md)）。ただし**間違ったイメージを書いた場合や、書き込み中に
> 電源が切れた場合**は、そのまま使えなくなることがあります。
> 心配な場合は [書き換えずに済ませる方法](01-symptoms.md) もあります。

---

## 何が変わるのか

ファーム全体（約 400 KB）のうち、**4 バイトの日付定数**を置き換え、
**バージョン表記**を書き換えます。実際に値が変わるのは **3 バイトだけ**です。

| 場所 | 変更前 | 変更後 | 意味 |
|---|---|---|---|
| `0x402F4` | `315964800` | `935280000` | 日付の基準時刻を 1024 週ぶん進める（4 バイトの定数） |
| `0x59C28` | `V1.13` | `V1.14` | 改造済みの印（5 バイトの文字列） |

実際に変わるバイトは `0x402F6`, `0x402F7`, `0x59C2C` の **3 バイト**です
（定数は 4 バイトですが、値が変わるのは上位 2 バイトだけです）。
これ以外は 1 バイトも変わりません。

---

## 用意するもの

| 必要なもの | 確認方法 |
|---|---|
| M-241 本体（電池入り・電源 ON） | OLED に何か表示される |
| USB ケーブル | 付属のもの |
| PC（Windows 11） | **実機検証済み**。Windows 10 と Linux / macOS は未検証 |
| Python 3.10 以上 | `python --version` |
| `DA_v3.8.4.bin` | 純正 `HoluxLoggerUtility` に同梱（このリポジトリには含まれません）。入手方法は [インストール](02-install.md#step-5--the-da-file-da_v384bin) |

---

## 手順 1 — デバイスが見えるか確認する

```bash
m241 ports
```

```
DEVICE            CAND  DESCRIPTION                       HWID
COM3              *     CP210x USB to UART Bridge         USB VID:PID=10C4:EA60
candidate port(s): COM3
```

**うまくいかない場合:** ケーブルを挿し直す、別の USB ポートを試す、`m241 ports` に何も出なければ CP210x ドライバが入っていません。

次に本体の情報を読みます。

```bash
m241 info
```

```
port     : COM3
release  : B-core_1.20
build    : 0043
model    : 01017-00F
sdk      : 1.0
chip     : MT3318
raw      : $PMTK705,B-core_1.20,0043,01017-00F,1.0*72
```

**ここで `release` と `model` が出れば、PC と本体は正しくつながっています。**
出ない場合は [トラブルシューティング](06-troubleshooting.md) へ。

---

## 手順 2 — バックアップを取る（読み取りのみ・安全）

```bash
m241 dump auto -o backup/stock.bin
```

```
port      : COM3
base addr : 0x00200000
size      : 406620 bytes
sha256    : <この個体のハッシュ>
profile   : v1.13
verified  : yes (two identical dumps)
header    : HOLUX_M-241 01017-00F release B-core 1.20 build 67 (no build date)
written   : backup\stock.bin
```

読み取り中は `123456/406620 bytes ( 30.4%)` のような進捗が stderr に表示されます
（ターミナルのときだけ）。`sha256` は**個体ごとに違う**ので、この例と一致しなくて正常です。

**このステップは本体に一切書き込みません。** 何度失敗しても壊れません。

`m241 dump` は既定で 2 回読み、ハッシュが一致することを確認します。
1 回目の読み取りが終わると、**電源を入れ直すよう促されます**。

```
power-cycle the logger now (so the read is from a fresh boot), then press Enter to dump again and compare hashes
```

この表示が出たら:

1. 電池を抜き、**約 10 秒待つ**
2. 電池を入れ直して、電源を ON にする
3. ターミナルに戻って **Enter** を押す

すると 2 回目の読み取りが始まり、2 つのハッシュが比べられます。一致しなければエラーになります。

**成功の見分け方:** `verified  : yes (two identical dumps)` と表示され、
`backup/stock.bin` が **406,620 バイト**になっている。
`sha256` がこのページの例と違っても正常です。

`backup/stock.bin` が既にあると上書きを拒否します。その場合は `--force` を付けるか、
別のファイル名を指定してください。

**所要時間の目安:** 約 40 秒

> **このファイルを消さないでください。** 何かあったときの唯一の復旧元です。

---

## 手順 3 — 修正版を作る（PC 内で完結）

まず何が変わるか確認します。

```bash
m241 patch backup/stock.bin -o backup/fixed.bin --dry-run
```

```
input  : backup\stock.bin
profile: v1.13 (Holux M-241 firmware V1.13)
version: V1.13 -> V1.14

  0x402F4  803dd512 -> 803dbf37  WNRO fix: shift the Unix epoch base forward by +619315200 s (+1024 weeks)
            changed: 0x402F6, 0x402F7
  0x59C28  56312e3133 -> 56312e3134  version marker: V1.13 -> V1.14
            changed: 0x59C2C

changed offsets: 0x402F6, 0x402F7, 0x59C2C
sha256 before : <この個体のハッシュ>
sha256 after  : <別のハッシュ>
dry run: nothing was written
```

問題なければ実行します。

```bash
m241 patch backup/stock.bin -o backup/fixed.bin
```

**成功の見分け方:** 次の 3 つがそろっていること。

- `profile: v1.13`（個体が V1.13 の場合）
- `changed offsets: 0x402F6, 0x402F7, 0x59C2C`（**3 バイト**）
- `backup/fixed.bin` ができている

`sha256 after` の値は個体ごとに違います。**このページの例と一致しなくて正常です。**

**所要時間の目安:** 1秒

---

## 手順 4 — 書き戻す（ここだけ危険）

```bash
m241 flash auto --da vendor/DA_v3.8.4.bin --fw backup/fixed.bin --backup backup/stock.bin
```

途中で確認を求められます。ツールが上に表示したパスとハッシュを確認し、
`Type WRITE to flash the logger, anything else aborts:` と出たら `WRITE` と入力してください。

```
port      : COM3
firmware  : backup\fixed.bin (406620 bytes)
sha256    : <修正版のハッシュ>
DA        : version 0x0308, flash 524288 bytes, id 0x49
flash     : written and the download agent was restarted
next      : power-cycle the logger and check the OLED shows V1.14
```

書き込み中は `123456/406620 bytes ( 30.4%)` のような進捗が stderr に表示されます。

**成功の見分け方:** `flash     : written and the download agent was restarted` と表示される。
そのあと**電源を入れ直して**（電池を抜いて約 10 秒）、手順 5 で NMEA の日付が今日に
なっていることを確認します。

> このツールは書き込み後に読み戻して照合はしません。確認は手順 5 で行います。
> `next` 行の「OLED に V1.14 と出る」は期待される動作ですが、画面での確認は未検証です（⚠️ 未検証）。

**所要時間の目安:** 2〜3分

> **この間、USB ケーブルを抜かないでください。** 本体を動かさないでください。

**失敗した場合:** [失敗したとき](04-recovery.md) へ。パニックにならないでください。
BootROM はマスク ROM なので消せません。電源を入れ直して自分のバックアップを書き戻せば、
たいていは復旧できます。ただし間違ったイメージや書き込み中の電源断では、使えなくなることがあります。

---

## 手順 5 — 確認する

`m241 info` はファームの版と型番を表示しますが、**日付は表示しません**。
日付は NMEA を直接見て確認します。`--stdout` を付けると NMEA がターミナルに流れます。

```bash
m241 nmea proxy auto --stdout
```

数秒待つと `$GPRMC` の行が出ます。**9 番目のフィールドが日付**（`ddmmyy`）です。

```
$GPRMC,060530.588,V,8960.0000,N,00000.0000,E,0.00,0.00,151126,,,N*..
                                                       ^^^^^^ 2026-11-15
```

（チェックサムは省略しています。）`Ctrl-C` で止められます。

**成功の見分け方:** `$GPRMC` の日付フィールドが今日の日付になっている。

**実機で確認済み:** 書き込み後、`$GPRMC` の日付が `151126`（2026-11-15）になりました。
OLED の日付表示は同じ計算経路なので直るはずですが、画面では未確認です（⚠️ 未検証）。
OLED で確認する場合は、電源を入れ直したあと画面の日付を見てください。

---

## 確認できていること・できていないこと

| 項目 | 状態 |
|---|---|
| バックアップ取得（2回、ハッシュ一致） | ✅ 実機で確認済み |
| パッチ適用（4バイトの定数を置換、実際に変わるのは3バイト） | ✅ 実機で確認済み |
| 書き込み（DA が再起動したことを確認） | ✅ 実機で確認済み |
| 書き込み後の起動 | ✅ 実機で確認済み |
| NMEA の日付が今日になる | ✅ 実機で確認済み |
| **OLED の日付表示** | ⚠️ 未検証（同じ計算経路なので直るはず） |
| **OLED の `V1.14` 表示** | ⚠️ 未検証 |
| **書き込み後に記録したログの日時** | ⚠️ 未検証（NMEA と同じ計算経路なので直るはず） |
| **Linux / macOS での実行** | ⚠️ 未検証 |

---

## 次に読む

- 失敗した → [失敗したとき](04-recovery.md)
- 書き換えたくない → [症状と原因](01-symptoms.md) の「書き換えずに済ませる方法」
- 用語がわからない → [早見表](07-quick-card.md)
