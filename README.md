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
```

最後にユーザーが beatoraja で再スキャン（F5）。

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
├── scripts/
│   ├── install_diffs.py         本体パイプライン（スコアリング + 配置）
│   ├── install_parents.py       親楽曲ダウンローダ
│   ├── prepare_haiku_input.py   ambiguous.jsonl を Haiku 用にスリム化
│   └── apply_haiku.py           Haiku の place/skip 判定を適用
└── README.md
```

## コマンドラインから直接使う

スキルを介さなくても、スクリプト単体で全部回せます。以下は引数の埋め方の例
（`<...>` は各自の環境に書き換え）。

```bash
# Linux / macOS
HEADER_URL=https://potechang.github.io/like_st/header.json
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
```

Windows PowerShell でも同様（変数は `$env:` か `$VAR=`、行継続は `` ` ``）。

再実行は無料：DLは `<state-dir>/downloads/`（差分）と
`<state-dir>/parent_downloads/`（親）に md5 / URL ベースでキャッシュされる。

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

## 既知の制約

- **Haiku 判定の暴走**: タイブレークができないケースで先頭フォルダにフォール
  バック配置しようとすることがあります。スキルプロンプトでも明示的に禁じて
  ますが、`apply_haiku.py` は判定結果を盲信するので、適用前に判定 JSON を
  目視確認したほうが安全。
- **フォルダ命名**は親BMSの `#ARTIST` / `#TITLE` を使用し、差分サフィックス
  （`[Eternity]` / `(SP …)`）を末尾から除去。既存フォルダがあれば `exists`
  として上書きしません。
- **songdata.db には触りません**。スキル実行後は beatoraja で再スキャン
  （F5）するか、起動時自動スキャンを有効化してください。
- **親キャッシュは direct URL ベース**。元の親URLが別の direct URL に
  解決されると（GDrive ID 変更時など）キャッシュキーが一致せず古いファイル
  が孤立します。容量を空けたいなら `parent_downloads/` は削除可。

## ライセンス

個人利用想定。スクリプトは MIT 相当、好きにどうぞ。
