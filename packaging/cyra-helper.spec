# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller ONE-DIR spec for CyraHelper.

Why one-dir: torch/diffusers ship large native DLLs (CUDA runtimes etc.).
One-dir keeps them as real files the loader can find, instead of unpacking
a one-file bundle into a temp dir on every launch. The debug twin
(CyraHelper-debug, console=True) ships alongside for crash logging.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ver = (ROOT / "helper" / "VERSION").read_text(encoding="utf-8").strip()

# Best-effort: collect torch's native DLLs when torch is importable.
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs

    torch_binaries = collect_dynamic_libs("torch")
except Exception:
    torch_binaries = []

a = Analysis(
    [str(ROOT / "helper" / "main.py")],
    pathex=[str(ROOT)],
    binaries=torch_binaries,
    datas=[(str(ROOT / "helper" / "VERSION"), "helper")],
    hiddenimports=[
        "torch",
        "diffusers",
        "diffusers.pipelines",
        "PIL",
        "PIL.Image",
        "requests",
        "psutil",
        "tkinter",
        "tkinter.ttk",
        "accelerate",
        "safetensors",
        "huggingface_hub",
        "helper",
        "helper.gui",
        "helper.worker",
        "helper.registration",
        "helper.tailscale",
        "helper.protocol",
        "helper.jobs",
        "helper.jobs.render",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CyraHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

debug_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CyraHelper-debug",
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
    debug_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CyraHelper",
)
