@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Building Pikafish Xiangqi GUI v1.0
echo ==========================================

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found.
  echo Install Python 3.11 or 3.12 and add Python to PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :fail
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "Pikafish Xiangqi" ^
  --collect-all PySide6 ^
  "pikafish_xiangqi.py"
if errorlevel 1 goto :fail

echo.
echo Build complete:
echo   %CD%\dist\Pikafish Xiangqi.exe
echo.
pause
exit /b 0

:fail
echo.
echo Build failed. Review the error above.
pause
exit /b 1
