@echo off
title ScreenCapture - uninstall
set "INSTALL_DIR=%LOCALAPPDATA%\ScreenCapture"
set "START_MENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo This removes ScreenCapture from %INSTALL_DIR%
echo and its Start Menu / Startup shortcuts. Your recordings and
echo screenshots (Videos\ScreenCapture, Pictures\ScreenCapture) are kept.
echo.
echo If the app is running, quit it first from its tray icon.
echo.
set /p CONFIRM="Type Y to continue: "
if /i not "%CONFIRM%"=="Y" exit /b 0

del /q "%START_MENU_DIR%\ScreenCapture.lnk" 2>nul
del /q "%STARTUP_DIR%\ScreenCapture.lnk" 2>nul
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
echo Done.
pause
