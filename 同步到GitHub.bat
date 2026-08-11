@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Git 自动同步脚本 ===
echo.

git add -A
if errorlevel 1 (
    echo [错误] git add 失败
    pause
    exit /b 1
)

set /p msg=请输入提交说明（直接回车用默认说明）:
if "%msg%"=="" set msg=auto commit %date% %time%

git commit -m "%msg%"
if errorlevel 1 (
    echo [提示] 没有需要提交的更改，或提交失败
    goto :push
)

:push
git push
if errorlevel 1 (
    echo [错误] 推送失败，请检查代理或网络
    pause
    exit /b 1
)

echo.
echo === 同步完成 ===
pause
