@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "FOLDER=%~1"
if "%FOLDER%"=="" set "FOLDER=."

echo ============================================
echo  Merge Word documents in: %FOLDER%
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel%==0 goto :USE_PYTHON

where py >nul 2>nul
if %errorlevel%==0 goto :USE_PY

echo Python not found.
echo Please install Python from https://www.python.org/downloads/
echo and make sure "Add to PATH" is checked.
echo.
pause
exit /b 1

:USE_PY
set "PY=py"
goto :RUN

:USE_PYTHON
set "PY=python"

:RUN
%PY% merge_docs.py "%FOLDER%" -r

echo.
echo Done. Press any key to close...
pause >nul
endlocal
