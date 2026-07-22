@echo off
REM ============================================================
REM  Build script for Fluorescence Channel Merger (Windows .exe)
REM ============================================================
REM  Run this from a Windows machine that has Python installed.
REM  It installs the required packages and uses PyInstaller to
REM  produce a single standalone .exe in the "dist" folder.
REM ============================================================

echo Installing required packages...
pip install -r requirements.txt

echo.
echo Building standalone executable...
pyinstaller --onefile --windowed --icon=FluorescenceChannelMerger.ico --name "FluorescenceChannelMerger" FluorescenceChannelMerger.py

echo.
echo ============================================================
echo Build complete. Your .exe is in the "dist" folder:
echo   dist\FluorescenceChannelMerger.exe
echo ============================================================
pause
