@echo off
set "PYSCRIPT=%~dp0sync_gdrive_images.py"
echo Installing requirements...
pip install firebase-admin google-api-python-client google-auth-httplib2 google-auth-oauthlib >nul 2>&1
echo Running sync...
python "%PYSCRIPT%"
if "%ERRORLEVEL%"=="0" (
  echo Done!
) else (
  echo Failed!
)
pause
