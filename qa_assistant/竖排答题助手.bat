@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python keju_vertical.py
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py keju_vertical.py
    ) else (
        echo Python not found. Please install Python 3 and add it to PATH.
    )
)
echo.
echo Program exited. Press any key to close.
pause >nul