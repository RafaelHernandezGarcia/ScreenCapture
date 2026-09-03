@echo off
setlocal enabledelayedexpansion
title ScreenCapture - Windows installer
:: "install.bat /quiet" = no pauses (for scripted installs)
set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"
echo ============================================================
echo   ScreenCapture - Windows installation
echo ============================================================
echo.
echo   Installs a private copy of the app with its own Python
echo   virtual environment (no system-wide packages touched):
echo.
echo     %LOCALAPPDATA%\ScreenCapture
echo.
echo   Adds a Start Menu entry and starts the app at login.
echo.

:: ------------------------------------------------------------------
:: 1. Folders
:: ------------------------------------------------------------------
set "SCRIPT_DIR=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\ScreenCapture"
set "START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

:: ------------------------------------------------------------------
:: 2. Find a Python 3.10+ interpreter (Windows Store stubs are skipped)
:: ------------------------------------------------------------------
set "PYTHON_EXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%PROGRAMFILES%\Python312\python.exe"
    "%PROGRAMFILES%\Python313\python.exe"
    "%PROGRAMFILES%\Python311\python.exe"
    "%PROGRAMFILES%\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python313\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if not defined PYTHON_EXE if exist %%P set "PYTHON_EXE=%%~P"
)
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PYTHON_EXE (
            echo %%P | findstr /i "WindowsApps" >nul || set "PYTHON_EXE=%%P"
        )
    )
)
if not defined PYTHON_EXE (
    echo ERROR: Python 3.10+ not found. Install it from python.org and re-run.
    pause
    exit /b 1
)
echo Using Python : %PYTHON_EXE%
echo Installing to: %INSTALL_DIR%
echo.

:: ------------------------------------------------------------------
:: 3. Ask a running copy to quit cleanly (over its single-instance port)
:: ------------------------------------------------------------------
"%PYTHON_EXE%" "%SCRIPT_DIR%main.py" --quit >nul 2>&1
if not errorlevel 1 (
    echo Asked the running copy of ScreenCapture to quit...
    ping -n 4 127.0.0.1 >nul
)
:: An OLD build (before Sep 2026) holds the port but ignores --quit: ask the user.
:check_running
"%PYTHON_EXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.3);sys.exit(0 if s.connect_ex(('127.0.0.1',47392)) else 1)" >nul 2>&1
if not errorlevel 1 (
    if defined QUIET (
        echo Waiting for the running copy to exit...
        ping -n 3 127.0.0.1 >nul
        goto :check_running
    )
    echo.
    echo A copy of ScreenCapture is still running. Right-click its tray icon,
    echo choose Exit / Quit ScreenCapture, then press any key to continue.
    pause >nul
    goto :check_running
)

:: ------------------------------------------------------------------
:: 4. Copy the app files
:: ------------------------------------------------------------------
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%INSTALL_DIR%\assets" mkdir "%INSTALL_DIR%\assets"
if not exist "%INSTALL_DIR%\sc" mkdir "%INSTALL_DIR%\sc"
copy /Y "%SCRIPT_DIR%*.py" "%INSTALL_DIR%\" >nul
copy /Y "%SCRIPT_DIR%sc\*.py" "%INSTALL_DIR%\sc\" >nul
copy /Y "%SCRIPT_DIR%requirements.txt" "%INSTALL_DIR%\" >nul
copy /Y "%SCRIPT_DIR%config.example.json" "%INSTALL_DIR%\" >nul
if exist "%SCRIPT_DIR%assets\*" copy /Y "%SCRIPT_DIR%assets\*" "%INSTALL_DIR%\assets\" >nul
:: keep an existing config.json (user preferences) - never overwrite it

:: ------------------------------------------------------------------
:: 5. Private virtual environment + dependencies
:: ------------------------------------------------------------------
if not exist "%INSTALL_DIR%\.venv\Scripts\python.exe" (
    echo Creating virtual environment...
    "%PYTHON_EXE%" -m venv "%INSTALL_DIR%\.venv"
    if errorlevel 1 (
        echo ERROR: could not create the virtual environment.
        pause
        exit /b 1
    )
)
echo Installing dependencies (first time takes 1-3 minutes)...
"%INSTALL_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%INSTALL_DIR%\.venv\Scripts\python.exe" -m pip install -r "%INSTALL_DIR%\requirements.txt" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check your network / proxy and re-run.
    pause
    exit /b 1
)

set "PYTHONW_EXE=%INSTALL_DIR%\.venv\Scripts\pythonw.exe"

:: ------------------------------------------------------------------
:: 6. Shortcuts: Start Menu (searchable) + Startup (auto-start at login)
:: ------------------------------------------------------------------
echo Creating shortcuts...
set "VBS_FILE=%TEMP%\sc_make_shortcuts.vbs"
> "%VBS_FILE%" echo Set oWS = WScript.CreateObject("WScript.Shell")
>> "%VBS_FILE%" echo Set oLink = oWS.CreateShortcut("%START_MENU_DIR%\ScreenCapture.lnk")
>> "%VBS_FILE%" echo oLink.TargetPath = "%PYTHONW_EXE%"
>> "%VBS_FILE%" echo oLink.Arguments = """%INSTALL_DIR%\main.py"""
>> "%VBS_FILE%" echo oLink.WorkingDirectory = "%INSTALL_DIR%"
>> "%VBS_FILE%" echo oLink.Description = "ScreenCapture - screenshots and screen recording"
>> "%VBS_FILE%" echo oLink.IconLocation = "%INSTALL_DIR%\assets\icon.ico"
>> "%VBS_FILE%" echo oLink.Save
>> "%VBS_FILE%" echo Set oLink = oWS.CreateShortcut("%STARTUP_DIR%\ScreenCapture.lnk")
>> "%VBS_FILE%" echo oLink.TargetPath = "%PYTHONW_EXE%"
>> "%VBS_FILE%" echo oLink.Arguments = """%INSTALL_DIR%\main.py"""
>> "%VBS_FILE%" echo oLink.WorkingDirectory = "%INSTALL_DIR%"
>> "%VBS_FILE%" echo oLink.Description = "ScreenCapture - auto start"
>> "%VBS_FILE%" echo oLink.IconLocation = "%INSTALL_DIR%\assets\icon.ico"
>> "%VBS_FILE%" echo oLink.Save
cscript //nologo "%VBS_FILE%"
del "%VBS_FILE%"

:: ------------------------------------------------------------------
:: 7. Launch it now
:: ------------------------------------------------------------------
echo.
echo ============================================================
echo   Installed.
echo ============================================================
echo   - Press the Windows key and type "ScreenCapture" to open it.
echo   - It starts automatically at login (tray menu can turn that off).
echo   - Press PrintScreen to capture. Change the key from the tray menu.
echo.
start "" /D "%INSTALL_DIR%" "%PYTHONW_EXE%" "%INSTALL_DIR%\main.py"
if not defined QUIET pause
