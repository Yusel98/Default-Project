@echo off
chcp 936 >nul
cd /d "%~dp0"

echo === Git auto sync ===
echo.

git add -A
if errorlevel 1 (
    echo [ERROR] git add failed
    pause
    exit /b 1
)

set /p msg=Enter commit message (Enter for default): 
if "%msg%"=="" set msg=auto commit

git commit -m "%msg%"
if errorlevel 1 (
    echo [INFO] nothing to commit
    goto :push
)

:push
git push
if errorlevel 1 (
    echo [ERROR] push failed
    pause
    exit /b 1
)

echo.
echo === sync done ===
pause
