# Building a Standalone .exe

PyInstaller has to build on the same OS you're targeting — it can't cross-compile
a Windows `.exe` from Linux or Mac. So this build needs to run **on a Windows
machine** (yours, a lab computer, whatever you'll actually use it on).

## One-time setup

1. Make sure Python 3.9+ is installed on the Windows machine.
   Check with: `python --version` in Command Prompt.
   If it's not installed, grab it from python.org — during install, check
   **"Add python.exe to PATH."**

2. Put these three files in the same folder:
   - `FluorescenceChannelMerger.py`
   - `requirements.txt`
   - `build_exe.bat`

## Build it

Double-click `build_exe.bat` (or run it from Command Prompt in that folder).
It will:
1. Install `numpy`, `tifffile`, `pillow`, and `pyinstaller`
2. Package the script into a single `.exe`
3. Drop the result at `dist\FluorescenceChannelMerger.exe`

The whole thing takes 1–3 minutes. That `.exe` is standalone — no Python
needed to run it, so you can copy it to any Windows computer (or hand it to
Pani) and just double-click it.

## Notes

- First run may be slow (a few seconds) — PyInstaller-built exes unpack to a
  temp folder on launch. This is normal.
- Windows Defender/SmartScreen may flag an unsigned `.exe` from an unknown
  publisher the first time it runs. Click "More info" → "Run anyway." This is
  standard for any unsigned, in-house tool and not a sign of an actual problem.
- If you want a custom icon, add `--icon=youricon.ico` to the `pyinstaller`
  line in `build_exe.bat` (needs to be a `.ico` file, in the same folder).
- If antivirus software on a shared lab computer quarantines the exe (some
  AV tools are aggressive about unsigned PyInstaller binaries), you may need
  to whitelist the file or run the `.py` version with Python installed instead.
