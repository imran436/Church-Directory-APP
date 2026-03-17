@echo off
title The Gathering Church - Directory Generator
color 0F
cls

echo.
echo  ================================================
echo   The Gathering Church - Directory Generator
echo  ================================================
echo.

cd /d "%~dp0"

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python is not installed.
    echo  Opening the Python download page now...
    echo.
    echo  Install it, then double-click this file again.
    echo  IMPORTANT: Check "Add Python to PATH" during install!
    echo.
    start https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    pause
    exit /b
)

REM ── Install dependencies (first run only) ────────────────────────────────────
if not exist ".setup_done" (
    echo  First time setup - installing components...
    echo  This takes about 1 minute. Please wait...
    echo.
    pip install -q requests keyring cryptography rapidfuzz Pillow Jinja2
    echo setup_done > .setup_done
    echo  Setup complete!
    echo.
)

REM ── Launch ───────────────────────────────────────────────────────────────────
echo  Starting...
pythonw main.py 2>nul
if errorlevel 1 python main.py
