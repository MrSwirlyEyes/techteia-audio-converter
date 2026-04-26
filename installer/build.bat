@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo   Techteia Audio Converter -- Developer Build Tool
echo.
echo   YOU run this once on YOUR machine to produce the
echo   installer .exe that grandma double-clicks to install.
echo   Grandma never needs Python, pip, or this script.
echo ============================================================
echo.

:: Step 1: Find or install Python
call :find_python
if "!PYTHON!"=="" (
    echo Python not found. Attempting automatic install...
    call :install_python
    call :find_python
)
if "!PYTHON!"=="" (
    echo.
    echo  Could not find or install Python automatically.
    echo  Please install Python 3.11 manually:
    echo    https://www.python.org/downloads/
    echo  Tick "Add Python to PATH" during setup, then re-run this script.
    pause & exit /b 1
)
echo [OK] Python: !PYTHON!
echo.

:: Step 2: Ensure pip is available
!PYTHON! -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip not found -- bootstrapping...
    !PYTHON! -m ensurepip --upgrade
)

:: Step 3: Download FFmpeg if not already present
if not exist "%~dp0ffmpeg\ffmpeg.exe" (
    call :download_ffmpeg
    if errorlevel 1 (
        echo.
        echo  ERROR: Could not download FFmpeg automatically.
        echo  Download manually from: https://github.com/BtbN/FFmpeg-Builds/releases
        echo  Extract ffmpeg.exe + ffprobe.exe into: %~dp0ffmpeg\
        pause & exit /b 1
    )
)
echo [OK] FFmpeg binaries ready.
echo.

:: Step 4: Install build dependencies
echo Installing pyinstaller, customtkinter, and Pillow...
!PYTHON! -m pip install pyinstaller customtkinter pillow --upgrade --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed.
    pause & exit /b 1
)
echo [OK] Dependencies installed.
echo.

:: Step 5: Run PyInstaller
set "ROOT=%~dp0"
set "DIST=%ROOT%dist"
set "BUILD=%ROOT%build"

echo Building standalone executable...
echo (This takes a minute or two -- grab a coffee.)
echo.

cd /d "%ROOT%"
!PYTHON! -m PyInstaller ^
  --onedir ^
  --windowed ^
  --name "Techteia Audio Converter" ^
  --distpath "%DIST%" ^
  --workpath "%BUILD%" ^
  --icon "icon.ico" ^
  --add-binary "ffmpeg\ffmpeg.exe;ffmpeg" ^
  --add-binary "ffmpeg\ffprobe.exe;ffmpeg" ^
  --add-data "icon.ico;." ^
  --add-data "logo.png;." ^
  --hidden-import customtkinter ^
  gui.py

if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller build failed. See output above for details.
    pause & exit /b 1
)
echo.
echo [OK] Executable built: %DIST%Techteia Audio Converter\

:: Step 6: Install Inno Setup if needed, then compile installer
echo.
call :find_innosetup
if "!ISCC!"=="" (
    echo Inno Setup not found. Installing automatically...
    call :install_innosetup
    call :find_innosetup
)
if "!ISCC!"=="" (
    echo  ERROR: Could not install Inno Setup automatically.
    echo  Download manually from https://jrsoftware.org/isinfo.php
    pause & exit /b 1
)

echo Compiling installer with Inno Setup...
"!ISCC!" "%~dp0techteia.iss"
if errorlevel 1 (
    echo  ERROR: Inno Setup compile failed. See output above.
    pause & exit /b 1
)

echo.
echo ============================================================
echo   SUCCESS!
echo   Installer: %~dp0TechteiaAudioConverter_Setup_v1.1.0.exe
echo   Send this .exe file to grandma -- that is all she needs!
echo ============================================================

echo.
pause
exit /b 0


:: ---------------------------------------------------------------------------
:: Subroutines
:: ---------------------------------------------------------------------------

:find_innosetup
set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
exit /b 0


:install_innosetup
where winget >nul 2>&1
if not errorlevel 1 (
    echo Trying winget...
    winget install JRSoftware.InnoSetup --silent --accept-source-agreements --accept-package-agreements
    if not errorlevel 1 exit /b 0
)

echo Downloading Inno Setup installer...
set "IS_URL=https://jrsoftware.org/download.php/is.exe"
set "IS_DL=%TEMP%\innosetup_installer.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%IS_URL%' -OutFile '%IS_DL%' -UseBasicParsing"
if not exist "%IS_DL%" ( echo  Download failed. & exit /b 1 )

echo Installing Inno Setup silently...
"%IS_DL%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
del /q "%IS_DL%" 2>nul
exit /b 0

:find_python
:: Check well-known install paths FIRST so we never accidentally pick up the
:: Windows Store python stub (which lives in WindowsApps and does nothing).
set "PYTHON="

for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%d ( set "PYTHON=%%d" & exit /b 0 )
)

:: py.exe is the Python Launcher -- it is never the Store stub
where py >nul 2>&1
if not errorlevel 1 ( set "PYTHON=py" & exit /b 0 )

:: Last resort: scan PATH but reject anything in WindowsApps
for /f "delims=" %%p in ('where python 2^>nul') do (
    if "!PYTHON!"=="" (
        echo "%%p" | findstr /i "WindowsApps" >nul 2>&1
        if errorlevel 1 ( set "PYTHON=%%p" )
    )
)
if not "!PYTHON!"=="" exit /b 0

for /f "delims=" %%p in ('where python3 2^>nul') do (
    if "!PYTHON!"=="" (
        echo "%%p" | findstr /i "WindowsApps" >nul 2>&1
        if errorlevel 1 ( set "PYTHON=%%p" )
    )
)
exit /b 0


:install_python
where winget >nul 2>&1
if not errorlevel 1 (
    echo Trying winget...
    winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
    if not errorlevel 1 (
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;!PATH!"
        exit /b 0
    )
)

echo Downloading Python 3.11 installer...
set "PY_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
set "PY_DL=%TEMP%\python_installer.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_DL%' -UseBasicParsing"
if not exist "%PY_DL%" ( echo  Download failed. & exit /b 1 )

echo Installing Python 3.11...
"%PY_DL%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
del /q "%PY_DL%" 2>nul
set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;!PATH!"
exit /b 0


:download_ffmpeg
echo FFmpeg not found. Downloading automatically (this may take a minute)...
set "FFURL=https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
set "FFZIP=%TEMP%\ffmpeg_build.zip"
set "FFTMP=%TEMP%\ffmpeg_extracted"
set "FFDEST=%~dp0ffmpeg"

if not exist "%FFDEST%" mkdir "%FFDEST%"

powershell -NoProfile -Command "Invoke-WebRequest -Uri '%FFURL%' -OutFile '%FFZIP%' -UseBasicParsing"
if not exist "%FFZIP%" ( echo  Download failed. & exit /b 1 )

echo Extracting ffmpeg.exe and ffprobe.exe...
powershell -NoProfile -Command "Expand-Archive -Path '%FFZIP%' -DestinationPath '%FFTMP%' -Force"

for /r "%FFTMP%" %%F in (ffmpeg.exe ffprobe.exe) do (
    copy /y "%%F" "%FFDEST%\" >nul
)

rd /s /q "%FFTMP%" 2>nul
del /q "%FFZIP%" 2>nul

if not exist "%FFDEST%\ffmpeg.exe"  ( echo  Extraction failed. & exit /b 1 )
if not exist "%FFDEST%\ffprobe.exe" ( echo  Extraction failed. & exit /b 1 )

echo [OK] FFmpeg downloaded and ready.
exit /b 0
