# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the BMS diff installer GUI.

Build:
    pyinstaller bms-diff-install.spec

Outputs:
    dist/bms-diff-install.exe   (single-file, no console)
"""

block_cipher = None


a = Analysis(
    ['scripts/run_gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'scripts',
        'scripts.install_diffs',
        'scripts.install_parents',
        'scripts.report',
        'scripts.songdb',
        'scripts.songdb.__main__',
        'scripts.songdb.hashing',
        'scripts.songdb.mode',
        'scripts.songdb.model',
        'scripts.songdb.parser_bms',
        'scripts.songdb.parser_bmson',
        'scripts.songdb.songdata',
        'scripts.songdb.writer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='bms-diff-install',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
