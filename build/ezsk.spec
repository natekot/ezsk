# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ezsk desktop app.

Usage:
    pyinstaller build/ezsk.spec

Produces:
    dist/ezsk/        (--onedir bundle)
    dist/ezsk.app     (macOS only, with --windowed)
"""
import platform
import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH).parent
SRC = ROOT / "ezsk"
WEB_STATIC = SRC / "web" / "static"

# Collect web static files
datas = [
    (str(WEB_STATIC), "ezsk/web/static"),
]

# Hidden imports — packages that ezsk imports dynamically
hiddenimports = [
    # Core
    "ezsk.cli",
    "ezsk.desktop",
    "ezsk.web.app",
    "ezsk.web.api",
    "ezsk.slskd_manager",
    "ezsk.setup",
    "ezsk.service",
    "ezsk.download",
    "ezsk.search",
    "ezsk.verify",
    "ezsk.ai_match",
    "ezsk.ai_parser",
    "ezsk.watch",
    "ezsk.filereader",
    "ezsk.dashboard",
    "ezsk.index",
    "ezsk.normalize",
    "ezsk.output",
    "ezsk.queryvariants",
    "ezsk.state",
    "ezsk.paths",
    "ezsk.config",
    "ezsk.client",
    "ezsk.display",
    "ezsk.updater",
    "ezsk.tray",
    # Web framework
    "starlette",
    "starlette.applications",
    "starlette.routing",
    "starlette.staticfiles",
    "starlette.requests",
    "starlette.responses",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "sse_starlette",
    "sse_starlette.sse",
    "python_multipart",
    # Audio verification (optional but included)
    "mutagen",
    "mutagen.flac",
    "mutagen.mp3",
    "mutagen.mp4",
    "mutagen.oggvorbis",
    "mutagen.wavpack",
    "mutagen.aiff",
    # Verify extras (may not be installed)
    "soundfile",
    "scipy",
    "scipy.fft",
    "numpy",
    # AI (optional)
    "anthropic",
    # Tray (optional, platform-specific)
    "rumps",
    "pystray",
    "PIL",
    # Other
    "platformdirs",
    "tqdm",
    "tomllib",
]

# Filter out packages that aren't installed
available_imports = []
for mod in hiddenimports:
    top = mod.split(".")[0]
    try:
        __import__(top)
        available_imports.append(mod)
    except ImportError:
        print(f"  [spec] Skipping optional: {mod}")

a = Analysis(
    [str(ROOT / "ezsk" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=available_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ezsk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No terminal window on macOS/Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon set per-platform in build scripts
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ezsk",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ezsk.app",
        icon=str(ROOT / "build" / "icon.icns") if (ROOT / "build" / "icon.icns").exists() else None,
        bundle_identifier="com.ezsk.app",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleName": "ezsk",
            "CFBundleDisplayName": "ezsk",
            "LSMinimumSystemVersion": "10.15",
            "NSHighResolutionCapable": True,
        },
    )
