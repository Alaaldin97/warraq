# PyInstaller spec for the Warraq conversion engine sidecar.
#
# Produces a directory bundle (dist/warraq-engine/) containing the engine
# executable and its runtime. The desktop shell spawns it and talks JSON-RPC
# over stdio.
#
#   python -m PyInstaller engine.spec --noconfirm
#
# Why --onedir and not --onefile:
#   * A onefile bundle extracts python3xx.dll to %TEMP% at every launch.
#     Windows Application Control (WDAC/AppLocker), standard on managed
#     corporate devices, blocks loading DLLs from temp - the engine dies with
#     "An Application Control policy has blocked this file". Verified on a
#     managed Windows 11 device during bring-up.
#   * onedir starts faster (no per-launch extraction).
#   * onedir supports binary-delta updates; onefile forces a full redownload.
#   * Each file can be individually signed, which keeps SmartScreen happy.
#
# UPX is deliberately disabled: compressed Python bundles are a common
# antivirus false-positive trigger (docs/ARCHITECTURE.md R3).

import os

block_cipher = None
ROOT = os.path.abspath(os.getcwd())

datas = [
    (os.path.join(ROOT, 'assets'), 'assets'),
]

# Ship only the language data the engine actually uses. eng_best (14.7 MB) is
# never selected, and osd (10.1 MB) was only needed by an orientation helper
# that has been removed.
TESSDATA_KEEP = {'ara.traineddata', 'eng.traineddata', 'configs', 'tessconfigs'}
tessdata = os.path.join(ROOT, 'tools', 'tessdata')
if os.path.isdir(tessdata):
    for entry in os.listdir(tessdata):
        if entry not in TESSDATA_KEEP:
            continue
        src = os.path.join(tessdata, entry)
        dest = os.path.join('tools', 'tessdata')
        if os.path.isdir(src):
            datas.append((src, os.path.join(dest, entry)))
        else:
            datas.append((src, dest))

# Modules the engine never touches. Excluding them keeps the sidecar within
# the installer budget.
excludes = [
    'tkinter', 'matplotlib', 'IPython', 'jupyter', 'notebook',
    'pandas', 'scipy', 'sqlalchemy', 'PySide6', 'PyQt5', 'PyQt6',
    'pytest', '_pytest', 'setuptools', 'pip', 'wheel', 'pydoc_data',
    'doctest', 'pdb', 'xmlrpc',
    # pulled in transitively but unused by the engine
    'lxml', 'PIL', 'Pillow',
]

a = Analysis(
    ['engine_main.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'kbo.rpc', 'kbo.cli', 'kbo.analyze', 'kbo.clean', 'kbo.ocr',
        'kbo.azure_ocr', 'kbo.arabic', 'kbo.arcorrect', 'kbo.extract',
        'kbo.fontfix', 'kbo.build', 'kbo.qa', 'kbo.score', 'kbo.report',
        'kbo.device', 'kbo.kfx', 'kbo.proc', 'kbo.batch',
        'arabic_reshaper', 'fontTools.ttLib', 'fontTools.ttLib.tables',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# OpenCV bundles a 27 MB FFmpeg video-I/O DLL. The engine only does still-image
# processing, so drop it.
a.binaries = [b for b in a.binaries
              if 'opencv_videoio_ffmpeg' not in os.path.basename(b[0]).lower()]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='warraq-engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='warraq-engine',
)
