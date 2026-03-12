@echo off
setlocal
chcp 65001 >nul

set "PROJECT_DIR=%~dp0"
set "PS1=%PROJECT_DIR%tools\run_image_sync.ps1"

if not exist "%PS1%" (
  echo [ERROR] 找不到腳本：%PS1%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [DONE] 圖片同步完成。
) else (
  echo [FAILED] 圖片同步失敗，錯誤碼：%RC%
)
pause
exit /b %RC%
