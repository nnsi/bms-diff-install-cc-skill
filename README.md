# bms-diff-install

[beatoraja](https://github.com/exch-bms2/beatoraja) 互換の難易度表から、BMS差分を
まとめて導入する Codex スキルです。未所持の親楽曲の取得、差分と親フォルダの
照合、曖昧ケースのモデル判定、`songdata.db` への直接登録まで扱います。

ファイル配置は既存ファイルを上書きしません。インストール依頼を受けた後は
dry-run で全件を分類し、途中の再確認で停止せず確定ケースまで配置します。

## Codex 版の判定構成

- 決定論的処理: Python スクリプトでダウンロード、BMS解析、#WAV照合
- 曖昧な配置先: `gpt-5.6-luna` がバッチ判定
- 静的HTMLで解けない親URL: `gpt-5.6-terra` と Codex Browser で解決
- 判定結果の適用: `apply_review.py` が候補外フォルダを拒否してから配置

Luna/Terra は判断ファイルだけを生成し、楽曲フォルダへの書き込みは検証用
スクリプトが担当します。

## 処理フロー

```text
難易度表 header.json
  │
  ├─ install_diffs.py --dry-run
  │    ├─ auto               確定配置
  │    ├─ ambiguous          Luna の配置先判定へ
  │    ├─ no_parent          親楽曲なし
  │    ├─ bundled_in_parent  差分が親パッケージ同梱
  │    └─ error              DL・解析エラー
  │
  ├─ install_parents.py      親URLを重複排除して取得
  │    └─ needs_browser      Luna → Terra/Browser でURL解決
  │
  ├─ install_diffs.py --apply
  ├─ prepare_review_input.py → review_decisions.json
  ├─ apply_review.py
  ├─ python -m scripts.songdb  （明示指定時のみ）
  └─ report.py → unrecovered.md / unrecovered.csv
```

## 前提

- Python 3.10 以降
- `.rar` / `.7z` / `.lzh` 展開時は 7-Zip CLI
- スキル運用時は Codex

Windows の標準的な 7-Zip は
`C:\Program Files\7-Zip\7z.exe` から自動検出されます。別の場所にある場合は
環境変数 `BMS_DIFF_7Z` に実行ファイルの絶対パスを設定します。`.zip` は Python
標準ライブラリだけで展開します。

## Codex スキルとして配置

リポジトリを Codex のスキルディレクトリへ clone します。

Linux / macOS:

```bash
git clone https://github.com/nnsi/bms-diff-install-cc-skill \
  "${CODEX_HOME:-$HOME/.codex}/skills/bms-diff-install"
```

Windows PowerShell:

```powershell
git clone https://github.com/nnsi/bms-diff-install-cc-skill `
  "$env:USERPROFILE\.codex\skills\bms-diff-install"
```

別のワークスペースで開発する構成では、同じパスへシンボリックリンクまたは
junction を作成します。Codex からは `$bms-diff-install` で明示起動でき、
難易度表からの差分インストール依頼では自動選択も有効です。

## CLI の基本形

以下の値を実環境に置き換えます。

- `HEADER_URL`: 難易度表の `header.json`
- `SONGDATA_DB`: beatoraja の `songdata.db`
- `MUSIC_ROOT`: `[ARTIST] TITLE` フォルダ群の親
- `STATE_DIR`: ログとダウンロードキャッシュの永続ディレクトリ

```bash
# 1. 判定のみ。MUSIC_ROOT には書き込まない
python scripts/install_diffs.py \
  --header-url "$HEADER_URL" \
  --songdata-db "$SONGDATA_DB" \
  --music-root "$MUSIC_ROOT" \
  --state-dir "$STATE_DIR" \
  --dry-run

# 2. 親楽曲URLの解決のみ
python scripts/install_parents.py \
  --state-dir "$STATE_DIR" \
  --music-root "$MUSIC_ROOT" \
  --dry-run

# 3. 親楽曲を取得
python scripts/install_parents.py \
  --state-dir "$STATE_DIR" \
  --music-root "$MUSIC_ROOT"

# 4. auto ケースを配置
python scripts/install_diffs.py ... --apply

# 5. ambiguous ケースのモデル判定用データを生成
python scripts/prepare_review_input.py --state-dir "$STATE_DIR"

# review_decisions.json を作成し、件数を記録して適用
python scripts/apply_review.py \
  --state-dir "$STATE_DIR" \
  --music-root "$MUSIC_ROOT"

# 6. 未配置レポート
python scripts/report.py --state-dir "$STATE_DIR"
```

難易度表の `header.json` / `data.json` は毎回再取得され、失敗時はキャッシュへ
フォールバックします。`--no-refresh-table` でキャッシュ固定になります。
差分は `STATE_DIR/downloads`、親楽曲は `STATE_DIR/parent_downloads` に保存され、
再実行時に再利用されます。

## GUI exe (Windows)

[Releases](https://github.com/nnsi/bms-diff-install-cc-skill/releases) の
`bms-diff-install.exe` は次を一括実行します。

1. dry-run
2. 親楽曲取得
3. 確定差分配置
4. `songdata.db` 更新
5. 未配置レポート生成

GUI 版は `ambiguous` / `needs_browser` をモデル判定せず、`unrecovered.md` に
保留として記録します。モデル判定部分は Codex スキルで実行します。

## リポジトリ構成

```text
.
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── ambiguous-placement.md
│   └── parent-resolution.md
├── scripts/
│   ├── install_diffs.py
│   ├── install_parents.py
│   ├── prepare_review_input.py
│   ├── apply_review.py
│   ├── report.py
│   ├── run_gui.py
│   └── songdb/
├── bms-diff-install.spec
└── .github/workflows/build-windows.yml
```

`prepare_haiku_input.py` と `apply_haiku.py` は旧 Claude Code 版との互換用に
残してあります。Codex の新規実行では review 系スクリプトを使用します。

## 判定しきい値

| 定数 | 既定値 | 用途 |
|---|---:|---|
| `AUTO_RATIO` | 0.95 | auto に必要な最良ヒット率 |
| `AUTO_GAP` | 0.20 | 1位と2位に必要な差 |
| `SKIP_RATIO` | 0.50 | 親未所持とみなす下限 |
| `WAV_SAMPLE_MAX` | 400 | 照合する #WAV 数の上限 |

同率 400/400 は共通サンプル名だけが一致している場合があるため、自動配置せず
Luna の artist/title 判定へ送ります。

## 安全性と状態ファイル

- `install_diffs.py` と `apply_review.py` は既存ファイルを上書きしません。
- `apply_review.py` は `ambiguous.jsonl` と照合し、候補外フォルダ、重複判定、
  欠落判定が1件でもあれば配置開始前に停止します。
- 親楽曲の重複排除後URL件数と概算容量は進捗として記録し、再確認では停止しません。
- 全件インストール時は beatoraja が停止中であることを確認し、バックアップ後に
  `songdata.db` へ登録します。起動中またはDB変更対象外ではF5更新を使用します。
- 状態JSON、CSV、Markdown、スキル文書は UTF-8 で保存します。BMS本体の
  Shift-JIS はパーサ側で扱います。

## ライセンス

`scripts/songdb/` は clean-room 実装で、同ディレクトリの MIT LICENSE が
適用されます。
