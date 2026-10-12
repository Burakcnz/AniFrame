@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PORT=8000"
set "PANEL=%SCRIPT_DIR%server_panel.py"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% "') do (
    taskkill /F /PID %%a >nul 2>&1
)

set "PYTHON_EXE="

if exist "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
) else if exist "%SCRIPT_DIR%venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%venv\Scripts\pythonw.exe"
) else (
    where pythonw.exe >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=pythonw.exe"
    ) else (
        where python.exe >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=python.exe"
        ) else (
            where python3.exe >nul 2>&1
            if !errorlevel! equ 0 (
                set "PYTHON_EXE=python3.exe"
            )
        )
    )
)

if "!PYTHON_EXE!"=="" (
    echo Python bulunamadi! Lutfen Python yukleyin.
    pause
    exit /b 1
)

cd /d "%SCRIPT_DIR%"

start "" "!PYTHON_EXE!" "%PANEL%"

if !errorlevel! neq 0 (
    echo Panel baslatilamadi! Hata kodu: !errorlevel!
    echo Hata loglari: server_panel.log
    pause
)

exit /b 0
