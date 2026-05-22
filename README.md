# bms-diff-install

[beatoraja](https://github.com/exch-bms2/beatoraja) 互換の難易度表から
BMS差分を一括インストールする Claude Code スキル。必要なら親楽曲もまとめて
DL します。面倒な部分（差分を正しい楽曲フォルダに当てる、曖昧ケースを
Haiku サブエージェントに丸投げ、Google Drive / Dropbox / web.archive.org /
manbow / venue.bmssearch の URL 正規化、Google Drive の virus-scan 確認
画面突破）はスクリプト側で処理します。

スキル起動は Claude Code 経由。中身は素の Python スクリプトなので手動
実行も可能。

## 動作の流れ

```
                 ┌───────────────────────────────────┐
                 │  難易度表の header.json           │
                 └──────────────┬────────────────────┘
                                ▼
              ┌──────────────────────────────────────────┐
              │  install_diffs.py  (--dry-run)           │
              │  songdata.db に未登録なエントリごとに:    │
              │  - url_diff を DL（GDrive 自動正規化）    │
              │  - #WAV キーサウンド参照を抽出            │
              │  - 各楽曲フォルダの音源ファイルとマッチング│
              │  - auto / ambiguous / no_parent に分類    │
              │  (配置後オプション: scripts/songdb で      │
              │   songdata.db に直接INSERTしてF5不要化)   │
              └────────────┬─────────────────────────────┘
                           │
                ┌──────────┼──────────┐
                ▼          ▼          ▼
              auto    ambiguous   no_parent
                │          │          │
                │          │   （任意）install_parents.py
                │          │          │  - 親URLで重複排除
                │          │          │  - アダプタ階層で解決
                │          │          │    (manbow / venue.bmssearch /
                │          │          │     GDrive file&folder / Dropbox /
                │          │          │     archive.org / ...)
                │          │          │  - DL（GDrive virus-scan 対応）
                │          │          │  - 7z で展開
                │          │          │  - 親の #ARTIST / #TITLE 読取
                │          │          │  - [ARTIST] TITLE/ に移動
                │          │          │  - 未対応ホストは needs_haiku
                │          │          ▼
                │          │    （Haiku が WebFetch で
                │          │     parent_haiku_urls.json 生成
                │          │     → --overrides で再実行）
                │          │          │
                │          │          ▼
                │          │   （親が増えたので
                │          │    install_diffs.py --apply を再実行）
                │          ▼
                │   prepare_haiku_input.py → Haiku 判定
                │   → apply_haiku.py が選ばれたフォルダへ配置
                ▼
  install_diffs.py --apply
  （親フォルダに譜面ファイルをコピー。既存ファイルは上書きしない）
                ▼
  python -m scripts.songdb --from-state-dir ...
  （配置済み譜面を songdata.db に直接INSERT、F5不要で
    次回 beatoraja 起動時に選曲画面に出現する）
```

## クイックスタート: GUI exe (Windows)

CLI を触らず「1ボタンで全部やる」だけしたい人向け。

1. [Releases](https://github.com/nnsi/bms-diff-install-cc-skill/releases) から
   `bms-diff-install.exe` を落とす（tag push 時に GitHub Actions が固める）
2. ダブルクリックで GUI 起動
3. 4項目を入れて「実行」を押す:
   - 難易度表 `header.json` URL
   - beatoraja の `songdata.db` パス
   - 楽曲フォルダ (music root) パス
   - 作業ディレクトリ (state dir) — どこでもよい空フォルダ
4. dry-run → 親DL → 差分配置 → `songdata.db` 更新 → 未配置リスト出力 を一気通貫で実行
5. 完了したら「unrecovered.md を開く」で未配置リストが見える

設定は `%USERPROFILE%\.bms-diff-install-gui.ini` に保存され、次回起動時に復元。

**exe 版の制約**:

- **`.rar` / `.7z` / `.lzh` 親アーカイブを展開するには 7-Zip CLI が別途必要**
  （公式インストーラを入れれば `C:\Program Files\7-Zip\7z.exe` が自動検出される）。
  `.zip` のみで済むケースなら不要。
- **ambiguous ケース（複数候補フォルダがあって自動判定できない差分）の
  Haiku 経由配置は exe では走らない**（Claude Code 環境前提のため）。それらは
  `unrecovered.md` に保留として列挙されるので、必要なら別途 Claude Code で
  `/bms-diff-install` スキルを使うか手動で配置する。
- Windows のみ。mac/linux は CLI を直叩きしてください。

ソースから自分でビルドしたい場合は `pip install pyinstaller` してから
`pyinstaller bms-diff-install.spec` で `dist\bms-diff-install.exe` が出ます。

## インストール

### 前提

- **Python 3.10+**（標準ライブラリのみ。追加パッケージ不要）
- **7-Zip CLI**（親アーカイブの `.rar` / `.7z` / `.lzh` 展開用。`.zip` のみで済むなら不要）
  - Windows: [公式インストーラ](https://www.7-zip.org/) を入れると `C:\Program Files\7-Zip\7z.exe` に置かれて自動検出されます
  - macOS: `brew install p7zip`（→ `7z` が PATH に通る）
  - Linux: 各ディストリで `p7zip-full` / `p7zip` 等のパッケージ
  - 自動検出が効かない場合は環境変数 `BMS_DIFF_7Z` に絶対パスを指定
- **Claude Code**（スキル経由でオーケストレーションさせる場合のみ。Python スクリプト単体実行ならなくても OK）

### スキルとして使う場合

Claude Code はユーザーホーム配下の `skills/` ディレクトリ（プラットフォーム依存：
Windows なら `%USERPROFILE%\.claude\skills`、macOS / Linux なら `~/.claude/skills`）
を見ます。そこに `bms-diff-install` という名前で配置されればスキルとして
認識されます。

**シンプルにそのまま clone**:

```bash
# Linux / macOS
git clone https://github.com/nnsi/bms-diff-install-cc-skill \
  ~/.claude/skills/bms-diff-install
```

```powershell
# Windows (PowerShell)
git clone https://github.com/nnsi/bms-diff-install-cc-skill `
  "$env:USERPROFILE\.claude\skills\bms-diff-install"
```

これで Claude Code から `/bms-diff-install` で呼べるようになります。
更新したくなったら `git pull` するだけ。

**別の場所で開発したい場合**（リポジトリを自分の workspace に置きつつ
スキルとしても認識させたい）はシンボリックリンク or junction を貼ります:

```bash
# Linux / macOS
ln -s /path/to/your/clone ~/.claude/skills/bms-diff-install
```

```powershell
# Windows (cmd or PowerShell with admin/Developer Mode で symlink、
#  もしくは管理者不要の junction を使う)
cmd /c mklink /J "$env:USERPROFILE\.claude\skills\bms-diff-install" `
  "D:\path\to\your\clone"
```

### コマンドラインだけで使う場合

スキルを介さずスクリプトを直接呼ぶなら、リポジトリをどこにでも clone して
`python scripts/install_diffs.py --help` 等で実行してください。

## リポジトリ構成

```
.
├── SKILL.md         Claude Code スキル本体（/bms-diff-install で起動）
├── bms-diff-install.spec        PyInstaller spec（GUI exe ビルド用）
├── .github/workflows/build-windows.yml  Windows exe を CI で固める
├── scripts/
│   ├── run_gui.py               tkinter GUI ランナー（exe 化のエントリポイント）
│   ├── install_diffs.py         本体パイプライン（スコアリング + 配置）
│   ├── install_parents.py       親楽曲ダウンローダ
│   ├── prepare_haiku_input.py   ambiguous.jsonl を Haiku 用にスリム化
│   ├── apply_haiku.py           Haiku の place/skip 判定を適用
│   ├── report.py                未配置差分の統合レポート (md + csv) 生成
│   └── songdb/                  jbms-parser互換 BMSパーサ + songdata.db書込
│       ├── hashing.py           生バイトmd5/sha256 + SongUtils互換crc32
│       ├── mode.py              Mode enum
│       ├── model.py             BMSModel/TimeLine/Note dataclasses
│       ├── parser_bms.py        BMS/BME/BML/PMS パーサ
│       ├── parser_bmson.py      BMSON (.bmson) パーサ
│       ├── chart_string.py      charthash (toChartString + Java double互換)
│       ├── songdata.py          BMSModel → song テーブル行
│       ├── writer.py            sqlite3 スキーマ + UPSERT
│       └── __main__.py          CLI（python -m scripts.songdb）
└── README.md
```

## コマンドラインから直接使う

スキルを介さなくても、スクリプト単体で全部回せます。以下は引数の埋め方の例
（`<...>` は各自の環境に書き換え）。

```bash
# Linux / macOS
HEADER_URL=<difficulty table の header.json の URL>     # 例: https://potechang.github.io/like_st/header.json
SONGDATA_DB=<beatoraja install>/songdata.db
MUSIC_ROOT=<beatoraja install>/../music
STATE_DIR=<scratch dir to keep logs and download cache>

# 1. ドライラン: 差分DLと配置先判定（music root には書き込まない）
python scripts/install_diffs.py \
  --header-url   "$HEADER_URL" \
  --songdata-db  "$SONGDATA_DB" \
  --music-root   "$MUSIC_ROOT" \
  --state-dir    "$STATE_DIR" \
  --dry-run

# 2. 親楽曲を一括インストール（1曲あたり 50〜150MB、レジューム可）
python scripts/install_parents.py \
  --state-dir   "$STATE_DIR" \
  --music-root  "$MUSIC_ROOT"

# 3. 差分を配置（親が揃ったので auto 判定が増える）
python scripts/install_diffs.py ... --apply

# 4. ambiguous が残ったら Haiku サブエージェントに渡す
#    （プロンプト雛形は SKILL.md 参照）→ 判定を適用
python scripts/prepare_haiku_input.py --state-dir "$STATE_DIR"
# （Haiku が haiku_decisions.json を書く）
python scripts/apply_haiku.py --state-dir "$STATE_DIR" --music-root "$MUSIC_ROOT"

# 5. （任意）未配置差分の統合レポートを出力
#    `unrecovered.md` (人間用、親URL単位でグループ化) と
#    `unrecovered.csv` (機械可読) を <state-dir> に書く
python scripts/report.py --state-dir "$STATE_DIR"
```

Windows PowerShell でも同様（変数は `$env:` か `$VAR=`、行継続は `` ` ``）。

再実行は無料：DLは `<state-dir>/downloads/`（差分）と
`<state-dir>/parent_downloads/`（親）に md5 / URL ベースでキャッシュされる。

## 失敗一覧の取得

`scripts/report.py --state-dir <STATE_DIR>` を実行すると、配置できなかった
差分を1ファイルにまとめた `unrecovered.md`（人間向け Markdown）と
`unrecovered.csv`（機械可読）が `<STATE_DIR>` 直下に生成されます。

Markdown 版は **親URL単位でグループ化**されており、同じ親に紐づく差分が
まとめて表示されるので「この親URLを手動で取りに行けば N 個まとめて解消」
というアクションが取りやすい形式。各差分について以下を含みます:

- md5（先頭8文字）/ Lv / Title / Artist
- カテゴリ: `no_parent`（親が未所持）/ `haiku_skip`（Haiku がスキップ判定）/ `dl_error`（DL/parse失敗）
- 親 URL（手で取りに行くなら参照する起点）
- 差分 URL（参考）
- 親側のステータス（installed / needs_haiku / error / never_attempted）と理由

## 判定しきい値

`install_diffs.py` 上部の定数:

| 定数              | 既定値 | 効果 |
|------------------|--------|------|
| `AUTO_RATIO`     | 0.95   | auto と判定するための最良スコア ÷ 総 #WAV 数 |
| `AUTO_GAP`       | 0.20   | 「明確」と言うための 1位 - 2位 の差（総数比） |
| `SKIP_RATIO`     | 0.50   | これを下回ると親未所持扱い |
| `WAV_SAMPLE_MAX` | 400    | スコアリングに使う #WAV 数の上限（性能向け） |

`auto` 判定は確実なケースだけ通すよう絞っています（100% ヒット + 2位との
大きな差）。微妙なやつは auto しない → Haiku に渡す、という設計。

## ホストアダプタ対応表（親インストール）

| パターン                                          | 解決方法 |
|--------------------------------------------------|----------|
| `*.zip` / `.rar` / `.7z` / `.lzh` の直リンク     | そのまま DL |
| `manbow.nothing.sh/event/event.cgi`              | HTML から `<Th>DownLoadAddress</Th><td><a href=URL>` を抽出 → 再帰 |
| `venue.bmssearch.net/...`                        | 直アーカイブ / GDrive / Dropbox リンクを抽出 → 再帰 |
| `bmssearch.net/bmses/...`                        | 直アーカイブリンクを抽出 |
| `drive.google.com/file/d/{ID}/...`               | `uc?export=download&id={ID}` に正規化 |
| `drive.google.com/drive/folders/{ID}`            | `embeddedfolderview` でファイル列挙 → 主アーカイブを選択 |
| `drive.google.com/uc?...`                        | そのまま（>100MB の virus-scan 確認画面も自動突破） |
| `drive.usercontent.google.com/...`               | そのまま |
| `dropbox.com/.../?dl=0`                          | `?dl=1` に書き換え |
| `dl.dropbox.com`, `dl.dropboxusercontent.com`    | そのまま |
| `archive.org/download/...`                       | そのまま |
| `web.archive.org/...`, `wayback.archive.org/...` | そのまま |
| `docs.google.com/uc?id=...`                      | GDrive 形式に正規化 |
| 上記以外                                          | `needs_haiku` ステータスでログに記録、Haiku 後段に回す |

JS 必須 / CAPTCHA 必要なホスト（Mega、AXFC、Wix ホスト、k-bms.com 等）は
`needs_haiku` として記録され、`parent_install_log.csv` に表示されます。
SKILL.md に Haiku 用プロンプト雛形があり、WebFetch で解決した URL を
`install_parents.py --overrides` 経由で戻します。

### 解決の3段階フォールバック

1. **Tier 1 — 決定論的アダプタ** (`install_parents.py` 内蔵)
   上の表のホストパターンに合うやつ。即座に解決。
2. **Tier 2 — Haiku + WebFetch サブエージェント**
   Tier 1 が `needs_haiku` で諦めたページを HTML 取得してDL URL を抽出。
   WebFetch で見える静的 HTML なら大抵 OK。
3. **Tier 3 — Sonnet + playwright サブエージェント** (推奨：頑固な長尾)
   Tier 2 でも取れなかったやつ。実体は:
   - web.archive.org のスナップショットが空 → **Wayback CDX API** で代替スナップショット列挙
   - MediaFire → playwright で `#downloadButton` を `waitForSelector` → `href` 抽出
   - getuploader → 一覧ページでセッションクッキーセット → DL ボタンクリック → `download` イベント captureして直接保存
   - Dropbox `scl/fi/?rlkey=...` で 400 → playwright で DL ボタンクリック (正しい rlkey を取得)
   - GDrive `/drive/u/N/folders/` → `/u/N/` 切除
   経験上 Haiku では「同 URL をそのまま返す」短絡が起きるので、この段階は
   `model: "sonnet"` 推奨。SKILL.md の Tier 3 セクションに詳細手順。

## 既知の制約

- **Haiku 判定の暴走**: タイブレークができないケースで先頭フォルダにフォール
  バック配置しようとすることがあります。スキルプロンプトでも明示的に禁じて
  ますが、`apply_haiku.py` は判定結果を盲信するので、適用前に判定 JSON を
  目視確認したほうが安全。
- **フォルダ命名**は親BMSの `#ARTIST` / `#TITLE` を使用し、差分サフィックス
  （`[Eternity]` / `(SP …)`）を末尾から除去。既存フォルダがあれば `exists`
  として上書きしません。
- **songdata.db は `scripts/songdb` で自動更新される**。配置直後に
  `python -m scripts.songdb --songdata-db ... --music-root ... --from-state-dir ...`
  を叩くと、差分譜面が直接 INSERT され **次回 beatoraja 起動時に
  選曲画面に即座に出現** する (F5 / 再スキャン不要)。BMS/BME/BML/PMS/BMSON
  全形式対応。`folder`/`parent` CRC32 は同じディレクトリの既存行から
  継承するので beatoraja の選曲ツリーと整合する。`charthash` は空文字
  (beatoraja 内部の重複検出に使われるが選曲には影響しない)。
  `scripts/songdb/` は **clean-room 実装** (SPEC.md と公開仕様のみベース、
  beatoraja / jbms-parser のソース未参照)、MIT ライセンス (LICENSE 同梱)。
- **親キャッシュは direct URL ベース**。元の親URLが別の direct URL に
  解決されると（GDrive ID 変更時など）キャッシュキーが一致せず古いファイル
  が孤立します。容量を空けたいなら `parent_downloads/` は削除可。

## ライセンス

個人利用想定。スクリプトは MIT 相当、好きにどうぞ。`scripts/songdb/` は
clean-room 実装で、専用 LICENSE ファイル (MIT) と provenance note を同梱。
