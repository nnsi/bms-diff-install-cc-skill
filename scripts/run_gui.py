"""
One-button GUI runner for the BMS diff installer pipeline.

Wraps install_diffs (dry-run) -> install_parents -> install_diffs (apply) ->
songdb (write to songdata.db) -> report into a single tkinter front-end.

ambiguous / needs_browser entries are deliberately left unplaced; they show up
in <state-dir>/unrecovered.md at the end.

Settings persist in %USERPROFILE%/.bms-diff-install-gui.ini.
"""

from __future__ import annotations

import configparser
import io
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from scripts import install_diffs, install_parents, report
from scripts.songdb import __main__ as songdb_cli


SEVEN_ZIP_URL = 'https://www.7-zip.org/'


CONFIG_PATH = os.path.join(
    os.environ.get('USERPROFILE') or os.path.expanduser('~'),
    '.bms-diff-install-gui.ini',
)


class QueueWriter(io.TextIOBase):
    """File-like that pushes everything written to a queue as ('log', text)."""

    def __init__(self, q: 'queue.Queue[tuple[str, str]]'):
        self._q = q

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if s:
            self._q.put(('log', s))
        return len(s)

    def flush(self) -> None:  # noqa: D401
        return None


def _run_step(name: str, fn, args: list, q: 'queue.Queue[tuple[str, str]]') -> int:
    """Run a CLI main(argv) with stdout/stderr captured to the queue."""
    q.put(('log', f'\n========== {name} ==========\n'))
    q.put(('log', '$ ' + ' '.join(str(a) for a in args) + '\n'))
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueWriter(q)
    t0 = time.time()
    try:
        rc = fn(args)
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:
        traceback.print_exc()
        rc = 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    q.put(('log', f'[{name}] exit={rc}  elapsed={time.time() - t0:.1f}s\n'))
    return rc


def run_pipeline(cfg: dict, q: 'queue.Queue[tuple[str, str]]') -> None:
    """Worker thread entry: dry-run -> parents -> apply -> songdb -> report."""
    header_url = cfg['header_url']
    songdata_db = cfg['songdata_db']
    music_root = cfg['music_root']
    state_dir = cfg['state_dir']

    os.makedirs(state_dir, exist_ok=True)

    common = [
        '--header-url', header_url,
        '--songdata-db', songdata_db,
        '--music-root', music_root,
        '--state-dir', state_dir,
    ]

    try:
        rc = _run_step('1. install_diffs --dry-run', install_diffs.main,
                       common + ['--dry-run'], q)
        if rc != 0:
            q.put(('done', f'dry-run failed (rc={rc})'))
            return

        rc = _run_step('2. install_parents', install_parents.main,
                       ['--state-dir', state_dir, '--music-root', music_root], q)
        if rc != 0:
            q.put(('log', f'[install_parents] non-zero exit ({rc}); continuing\n'))

        rc = _run_step('3. install_diffs --apply', install_diffs.main,
                       common + ['--apply'], q)
        if rc != 0:
            q.put(('done', f'apply failed (rc={rc})'))
            return

        rc = _run_step('4. songdb --from-state-dir', songdb_cli.main,
                       ['--songdata-db', songdata_db, '--music-root', music_root,
                        '--from-state-dir', state_dir], q)
        if rc != 0:
            q.put(('log', f'[songdb] non-zero exit ({rc}); continuing\n'))

        rc = _run_step('5. report', report.main,
                       ['--state-dir', state_dir], q)

        q.put(('done', 'finished'))
    except Exception:
        q.put(('log', traceback.format_exc()))
        q.put(('done', 'crashed'))


# ---------- GUI ----------

class App:
    LABELS = [
        ('header_url', '難易度表 header.json URL', 'entry'),
        ('songdata_db', 'songdata.db のパス', 'file'),
        ('music_root', '楽曲フォルダ (music root)', 'dir'),
        ('state_dir', '作業ディレクトリ (state dir)', 'dir'),
    ]

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('BMS Diff Installer')
        root.geometry('820x580')

        self.vars: dict[str, tk.StringVar] = {k: tk.StringVar() for k, _, _ in self.LABELS}
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_form(root)
        self._build_log(root)
        self._build_buttons(root)

        self._load_config()
        self.root.after(100, self._drain_queue)

    def _build_form(self, root: tk.Tk) -> None:
        frm = ttk.LabelFrame(root, text='設定')
        frm.pack(fill='x', padx=10, pady=8)
        for row, (key, label, kind) in enumerate(self.LABELS):
            ttk.Label(frm, text=label, width=28, anchor='w').grid(
                row=row, column=0, padx=6, pady=4, sticky='w')
            entry = ttk.Entry(frm, textvariable=self.vars[key])
            entry.grid(row=row, column=1, padx=6, pady=4, sticky='ew')
            if kind == 'file':
                ttk.Button(frm, text='参照…', width=8,
                           command=lambda k=key: self._pick_file(k)).grid(row=row, column=2, padx=4)
            elif kind == 'dir':
                ttk.Button(frm, text='参照…', width=8,
                           command=lambda k=key: self._pick_dir(k)).grid(row=row, column=2, padx=4)
        frm.columnconfigure(1, weight=1)

    def _build_log(self, root: tk.Tk) -> None:
        frm = ttk.LabelFrame(root, text='ログ')
        frm.pack(fill='both', expand=True, padx=10, pady=8)
        self.log = tk.Text(frm, wrap='word', font=('Consolas', 9))
        self.log.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(frm, command=self.log.yview)
        sb.pack(side='right', fill='y')
        self.log.config(yscrollcommand=sb.set, state='disabled')

    def _build_buttons(self, root: tk.Tk) -> None:
        bar = ttk.Frame(root)
        bar.pack(fill='x', padx=10, pady=(0, 10))
        self.run_btn = ttk.Button(bar, text='実行', command=self._on_run)
        self.run_btn.pack(side='left')
        self.open_btn = ttk.Button(bar, text='unrecovered.md を開く',
                                   command=self._open_unrecovered, state='disabled')
        self.open_btn.pack(side='left', padx=8)
        self.status = ttk.Label(bar, text='idle', foreground='gray')
        self.status.pack(side='right')

    # ---- config ----

    def _load_config(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return
        cp = configparser.ConfigParser()
        try:
            cp.read(CONFIG_PATH, encoding='utf-8')
        except Exception:
            return
        if 'paths' not in cp:
            return
        for k in self.vars:
            if k in cp['paths']:
                self.vars[k].set(cp['paths'][k])

    def _save_config(self) -> None:
        cp = configparser.ConfigParser()
        cp['paths'] = {k: v.get() for k, v in self.vars.items()}
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                cp.write(f)
        except Exception:
            pass

    # ---- pickers ----

    def _pick_file(self, key: str) -> None:
        p = filedialog.askopenfilename(title=key, initialfile=self.vars[key].get() or None)
        if p:
            self.vars[key].set(p)

    def _pick_dir(self, key: str) -> None:
        p = filedialog.askdirectory(title=key, initialdir=self.vars[key].get() or None)
        if p:
            self.vars[key].set(p)

    # ---- run ----

    def _on_run(self) -> None:
        cfg = {k: v.get().strip() for k, v in self.vars.items()}
        missing = [k for k, v in cfg.items() if not v]
        if missing:
            messagebox.showerror('入力不足', '次の項目が未入力:\n' + '\n'.join(missing))
            return
        if not os.path.exists(cfg['songdata_db']):
            messagebox.showerror('songdata.db', f'songdata.db が見つかりません:\n{cfg["songdata_db"]}')
            return
        if not os.path.isdir(cfg['music_root']):
            messagebox.showerror('music root', f'音楽フォルダが見つかりません:\n{cfg["music_root"]}')
            return

        self._save_config()
        self._append_log(f'config saved to {CONFIG_PATH}\n')

        self.run_btn.config(state='disabled')
        self.open_btn.config(state='disabled')
        self.status.config(text='running…', foreground='blue')

        self.worker = threading.Thread(target=run_pipeline, args=(cfg, self.q), daemon=True)
        self.worker.start()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self._append_log(payload)
                elif kind == 'done':
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _append_log(self, text: str) -> None:
        self.log.config(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.config(state='disabled')

    def _on_done(self, msg: str) -> None:
        self.run_btn.config(state='normal')
        self.status.config(text=msg, foreground='green' if msg == 'finished' else 'red')
        unrec = os.path.join(self.vars['state_dir'].get(), 'unrecovered.md')
        if os.path.exists(unrec):
            self.open_btn.config(state='normal')

    def _open_unrecovered(self) -> None:
        path = os.path.join(self.vars['state_dir'].get(), 'unrecovered.md')
        if not os.path.exists(path):
            messagebox.showinfo('unrecovered.md', 'まだ生成されていません')
            return
        try:
            os.startfile(path)  # Windows
        except AttributeError:
            subprocess.Popen(['xdg-open', path])


def _ensure_7z() -> None:
    """Hard-fail at startup if 7-Zip CLI is not available.

    Parent BMS archives are commonly .rar / .7z / .lzh — none of which Python's
    stdlib can extract. We re-use install_parents._resolve_7z() (which checks
    PATH, the BMS_DIFF_7Z env var, and standard Windows install paths)."""
    if install_parents._resolve_7z():
        return
    root = tk.Tk()
    root.withdraw()
    msg = (
        '7-Zip CLI が見つかりません。\n\n'
        '本ツールは親アーカイブ (.rar / .7z / .lzh) の展開に 7-Zip CLI を必要とします。\n\n'
        '今すぐ 7-Zip のダウンロードページを開きますか?\n'
        '(インストール後、本アプリを再起動してください)'
    )
    if messagebox.askyesno('7-Zip が必要です', msg):
        try:
            webbrowser.open(SEVEN_ZIP_URL)
        except Exception:
            pass
    root.destroy()
    sys.exit(1)


def main() -> int:
    _ensure_7z()
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
