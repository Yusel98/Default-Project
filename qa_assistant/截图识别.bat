@echo off
chcp 65001 >nul
title Screenshot Answer
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY=py"
    ) else (
        echo [ERROR] Python not found. Please install Python and add to PATH.
        pause
        exit /b 1
    )
)

%PY% answer_image.py
echo.
echo Done.
pause
