@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  ScreenCapture Final Installation
echo ========================================
echo.

:: 1. Setup Directories
set "SCRIPT_DIR=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\ScreenCapture"
set "START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

:: 2. Find Python
set "PYTHON_EXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%PROGRAMFILES%\Python313\python.exe"
    "%PROGRAMFILES%\Python312\python.exe"
    "%PROGRAMFILES%\Python311\python.exe"
    "%PROGRAMFILES%\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%P set "PYTHON_EXE=%%~P" && goto :found_python
)

:found_python
if "%PYTHON_EXE%"=="" (
    echo ERROR: Python not found. Please install Python.
    pause
    exit /b 1
)

:: Get pythonw.exe (The "No Console" version of Python)
set "PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%"

echo Found Python: %PYTHON_EXE%
echo Installing to: %INSTALL_DIR%

:: 3. Copy Files
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%INSTALL_DIR%\assets" mkdir "%INSTALL_DIR%\assets"
copy /Y "%SCRIPT_DIR%*.py" "%INSTALL_DIR%\" >nul
copy /Y "%SCRIPT_DIR%requirements.txt" "%INSTALL_DIR%\" >nul
if exist "%SCRIPT_DIR%assets\*" copy /Y "%SCRIPT_DIR%assets\*" "%INSTALL_DIR%\assets\" >nul

:: 4. Create Start Menu Shortcut (Searchable)
echo Creating Start Menu shortcut...
set "VBS_FILE=%TEMP%\create_shortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS_FILE%"
echo sLinkFile = "%START_MENU_DIR%\ScreenCapture.lnk" >> "%VBS_FILE%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS_FILE%"
echo oLink.TargetPath = "%PYTHONW_EXE%" >> "%VBS_FILE%"
echo oLink.Arguments = """%INSTALL_DIR%\main.py""" >> "%VBS_FILE%"
echo oLink.WorkingDirectory = "%INSTALL_DIR%" >> "%VBS_FILE%"
echo oLink.Description = "ScreenCapture Tool" >> "%VBS_FILE%"
if exist "%INSTALL_DIR%\assets\icon.png" echo oLink.IconLocation = "%INSTALL_DIR%\assets\icon.png" >> "%VBS_FILE%"
echo oLink.Save >> "%VBS_FILE%"

cscript //nologo "%VBS_FILE%"
del "%VBS_FILE%"

:: 5. Create Startup Shortcut (Auto-start on boot)
echo Creating Startup shortcut...
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_FILE=%TEMP%\create_startup.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS_FILE%"
echo sLinkFile = "%STARTUP_DIR%\ScreenCapture.lnk" >> "%VBS_FILE%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS_FILE%"
echo oLink.TargetPath = "%PYTHONW_EXE%" >> "%VBS_FILE%"
echo oLink.Arguments = """%INSTALL_DIR%\main.py""" >> "%VBS_FILE%"
echo oLink.WorkingDirectory = "%INSTALL_DIR%" >> "%VBS_FILE%"
echo oLink.Description = "ScreenCapture - Auto Start" >> "%VBS_FILE%"
if exist "%INSTALL_DIR%\assets\icon.png" echo oLink.IconLocation = "%INSTALL_DIR%\assets\icon.png" >> "%VBS_FILE%"
echo oLink.Save >> "%VBS_FILE%"

cscript //nologo "%VBS_FILE%"
del "%VBS_FILE%"

echo.
echo ========================================
echo  Success! 
echo ========================================
echo 1. You can now press Windows Key and type "ScreenCapture" to open it.
echo 2. The black command window will NO LONGER appear.
echo 3. The app will auto-start when you restart your computer.
echo.
pause