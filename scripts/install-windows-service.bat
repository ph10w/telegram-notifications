@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SERVICE_NAME=TelegramVoiceForwarder"
set "DISPLAY_NAME=Telegram Voice Forwarder"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "ENV_FILE=%PROJECT_DIR%\.env"
set "LOG_DIR=%PROJECT_DIR%\data\logs"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not change to the project directory:
    echo   %PROJECT_DIR%
    exit /b 1
)

echo Installing %DISPLAY_NAME% from:
echo   %PROJECT_DIR%
echo.

powershell.exe -NoProfile -Command ^
  "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 1 }"
if not errorlevel 1 goto :elevated

if defined GSUDO_EXE goto :check_gsudo_path
where.exe gsudo.exe >nul 2>&1
if errorlevel 1 goto :gsudo_missing
set "GSUDO_EXE=gsudo.exe"
goto :launch_elevated

:check_gsudo_path
if not exist "%GSUDO_EXE%" goto :gsudo_missing

:launch_elevated
echo Administrator rights are required. Restarting through gsudo...
"%GSUDO_EXE%" --chdir "%PROJECT_DIR%" "%~f0" %*
set "GSUDO_EXIT=%ERRORLEVEL%"
if "%GSUDO_EXIT%"=="999" echo ERROR: gsudo could not elevate the installer.
exit /b %GSUDO_EXIT%

:elevated

if not exist "%PYTHON_EXE%" (
    echo ERROR: Virtual-environment Python was not found:
    echo   %PYTHON_EXE%
    echo Create the environment and install the project first:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -e .
    exit /b 1
)

if not exist "%ENV_FILE%" (
    echo ERROR: Configuration file was not found:
    echo   %ENV_FILE%
    echo Copy .env.example to .env and configure it first.
    exit /b 1
)

set "SESSION_BASE=data\telegram-monitor"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr.exe /b /c:"TELEGRAM_SESSION=" "%ENV_FILE%"`) do set "SESSION_BASE=%%B"
for %%I in ("%SESSION_BASE%") do set "SESSION_FILE=%%~fI.session"

if not exist "%SESSION_FILE%" (
    echo ERROR: The authorized Telegram session was not found:
    echo   %SESSION_FILE%
    echo Run this command interactively before installing the service:
    echo   tg-setup list-chats
    exit /b 1
)

if defined SHAWL_EXE goto :check_shawl
set "SHAWL_EXE=%PROJECT_DIR%\tools\shawl.exe"
if exist "%SHAWL_EXE%" goto :check_shawl

set "SHAWL_EXE="
for /f "delims=" %%I in ('where.exe shawl.exe 2^>nul') do if not defined SHAWL_EXE set "SHAWL_EXE=%%I"
if not defined SHAWL_EXE goto :shawl_missing

:check_shawl
"%SHAWL_EXE%" --version >nul 2>&1
if errorlevel 1 goto :shawl_missing

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
    echo ERROR: Could not create the log directory:
    echo   %LOG_DIR%
    exit /b 1
)

sc.exe query "%SERVICE_NAME%" >nul 2>&1
if errorlevel 1 goto :install_service

echo ERROR: A service named %SERVICE_NAME% already exists.
echo This installer does not modify or migrate existing services.
echo Remove the existing service manually before running this installer.
exit /b 1

:install_service
echo Creating Windows service %SERVICE_NAME%...
"%SHAWL_EXE%" add ^
  --name "%SERVICE_NAME%" ^
  --restart ^
  --restart-delay 5000 ^
  --stop-timeout 15000 ^
  --kill-process-tree ^
  --cwd "%PROJECT_DIR%" ^
  --log-dir "%LOG_DIR%" ^
  --log-as "shawl" ^
  --log-cmd-as "service" ^
  --log-rotate "bytes=10485760" ^
  --log-retain 5 ^
  --env "PYTHONUNBUFFERED=1" ^
  -- "%PYTHON_EXE%" -m tg_forwarder run
if errorlevel 1 goto :install_failed

echo Configuring service...
sc.exe config "%SERVICE_NAME%" start= auto DisplayName= "%DISPLAY_NAME%" >nul
if errorlevel 1 goto :configure_failed
sc.exe description "%SERVICE_NAME%" "Monitors Telegram groups and copies matching voice messages to a private channel." >nul
if errorlevel 1 goto :configure_failed

sc.exe failure "%SERVICE_NAME%" reset= 86400 actions= restart/5000/restart/15000/restart/30000 >nul
if errorlevel 1 goto :configure_failed
sc.exe failureflag "%SERVICE_NAME%" 1 >nul
if errorlevel 1 goto :configure_failed

echo Starting service...
sc.exe start "%SERVICE_NAME%" >nul
if errorlevel 1 goto :start_failed

echo.
echo %DISPLAY_NAME% was installed and started successfully.
echo Service name: %SERVICE_NAME%
echo Application log: %LOG_DIR%\service_rCURRENT.log
echo Shawl log:       %LOG_DIR%\shawl_rCURRENT.log
echo.
sc.exe query "%SERVICE_NAME%"
exit /b 0

:shawl_missing
echo ERROR: Shawl was not found.
echo Put shawl.exe in "%PROJECT_DIR%\tools", add it to PATH,
echo or set SHAWL_EXE to its full path.
echo Shawl releases: https://github.com/mtkennerly/shawl/releases
exit /b 1

:gsudo_missing
echo ERROR: Administrator rights are required and gsudo was not found.
echo Install it with:
echo   winget install gerardog.gsudo
echo Then restart the terminal, add gsudo.exe to PATH, or set GSUDO_EXE
echo to its full path. Alternatively, run this script from an elevated terminal.
echo Documentation: https://gerardog.github.io/gsudo/docs/usage
exit /b 1

:install_failed
echo ERROR: Shawl could not create the service.
exit /b 1

:configure_failed
echo ERROR: The service was created, but its configuration could not be completed.
echo Remove the incomplete service if necessary:
echo   sc.exe delete "%SERVICE_NAME%"
exit /b 1

:start_failed
echo ERROR: The service was configured but could not be started.
echo Inspect the service and Shawl logs:
echo   sc.exe query "%SERVICE_NAME%"
echo   %LOG_DIR%\shawl_rCURRENT.log
exit /b 1
