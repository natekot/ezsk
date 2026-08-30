@echo off
setlocal

REM Build ezsk.exe for Windows
REM
REM Prerequisites:
REM   pip install pyinstaller
REM   pip install ezsk[web,verify]
REM
REM Usage:
REM   build\build_windows.bat
REM
REM Output:
REM   dist\ezsk\      — onedir bundle with ezsk.exe

set SCRIPT_DIR=%~dp0
set ROOT=%SCRIPT_DIR%..

echo === Building ezsk for Windows ===
echo Root: %ROOT%

cd /d %ROOT%

REM Ensure PyInstaller is available
where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Clean previous builds
if exist dist\ezsk rmdir /s /q dist\ezsk

REM Build
echo Running PyInstaller...
pyinstaller build\ezsk.spec --noconfirm

REM Download slskd and bundle it
echo Downloading slskd for bundling...
python -c "from ezsk.slskd_manager import download_slskd; download_slskd(verbose=True)"

for /f "delims=" %%i in ('python -c "from ezsk.slskd_manager import _slskd_binary_path; print(_slskd_binary_path())"') do set SLSKD_BIN=%%i
for %%i in ("%SLSKD_BIN%") do set SLSKD_DIR=%%~dpi

if exist "%SLSKD_BIN%" (
    REM Copy the whole release, not just the binary: slskd resolves wwwroot,
    REM config, and etc relative to its own directory and will not start without them.
    echo Bundling slskd...
    if exist dist\ezsk (
        mkdir dist\ezsk\bin 2>nul
        xcopy /E /I /Y "%SLSKD_DIR%*" dist\ezsk\bin\
        del /Q dist\ezsk\bin\version.txt 2>nul
    )
) else (
    echo WARNING: slskd binary not found, skipping bundle
)

echo.
echo === Build complete ===
if exist dist\ezsk (
    echo   dist\ezsk\ezsk.exe
)
echo.
echo To create an installer, use NSIS or Inno Setup with dist\ezsk\ as source.

endlocal
