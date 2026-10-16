# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the macOS app. Run it with ./build_mac.sh, which puts
# the result at "dist-mac/Fluorescence Channel Merger.app".
# (The Windows build uses FluorescenceChannelMerger.spec / build_exe.bat.)

APP_VERSION = '4.0'


a = Analysis(
    ['FluorescenceChannelMerger.py'],
    pathex=[],
    binaries=[],
    datas=[('FluorescenceChannelMerger.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FluorescenceChannelMerger',
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
    icon=['FluorescenceChannelMerger.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FluorescenceChannelMerger',
)
app = BUNDLE(
    coll,
    name='Fluorescence Channel Merger.app',
    icon='FluorescenceChannelMerger.icns',
    bundle_identifier='edu.rit.gaborski.fluorescencechannelmerger',
    version=APP_VERSION,
    info_plist={
        'CFBundleDisplayName': 'Fluorescence Channel Merger',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,       # sharp on Retina displays
        'NSRequiresAquaSystemAppearance': False,  # allow dark mode
        'LSMinimumSystemVersion': '11.0',
    },
)
