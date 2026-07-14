@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
title MeaPet

set "ERR=0"
set "MEAPET_PY=3.12"
set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "UV_CMD="
set "PY_CMD="
set "SYS_PY="

if exist "%~dp0.python-version" (
    for /f "usebackq tokens=* delims=" %%i in ("%~dp0.python-version") do (
        set "MEAPET_PY=%%i"
        goto py_ver_done
    )
)
:py_ver_done
for /f "delims=" %%i in ("!MEAPET_PY!") do set "MEAPET_PY=%%i"

if not defined UV_INDEX_URL set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
if not defined UV_PYTHON_INSTALL_MIRROR set "UV_PYTHON_INSTALL_MIRROR=https://ghproxy.com/https://github.com/astral-sh/python-build-standalone/releases/download"

if not exist "pet.py" goto missing_pet
if not exist "linux_requirements.txt" goto missing_req
goto have_files

:missing_pet
echo [MeaPet] missing pet.py - run from project root
set "ERR=1"
goto end

:missing_req
echo [MeaPet] missing linux_requirements.txt
set "ERR=1"
goto end

:have_files
if exist "%VENV_PY%" (
    set "PY_CMD=%VENV_PY%"
    echo [MeaPet] using .venv
    goto dep_check
)

echo [MeaPet] no .venv, creating...
call :ensure_uv
if defined UV_CMD (
    echo [MeaPet] uv: !UV_CMD!
    echo [MeaPet] uv venv Python !MEAPET_PY!
    "!UV_CMD!" venv --python "!MEAPET_PY!" "%VENV_DIR%"
    if errorlevel 1 (
        echo [MeaPet] uv venv failed, retry Python 3.12 official mirror
        set "UV_PYTHON_INSTALL_MIRROR="
        "!UV_CMD!" venv --python 3.12 "%VENV_DIR%"
    )
    if errorlevel 1 (
        echo [MeaPet] uv failed, try system Python
        call :create_venv_with_system_python
    )
) else (
    echo [MeaPet] no uv, try system Python
    call :create_venv_with_system_python
)

if not exist "%VENV_PY%" goto venv_fail
set "PY_CMD=%VENV_PY%"
echo [MeaPet] .venv ready
call :install_deps
if errorlevel 1 goto deps_fail
goto ready

:venv_fail
echo [MeaPet] failed to create .venv
echo.
echo Fix options:
echo   1. install uv: https://docs.astral.sh/uv/getting-started/installation/
echo      powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo   2. set MEAPET_ALLOW_DOWNLOAD=1 then re-run
echo   3. install Python 3.10+ with Add to PATH ^(local VITS recommends 3.10-3.12^)
echo   4. manual:
echo      uv python install 3.12
echo      uv venv --python 3.12 .venv
echo      uv pip install -r linux_requirements.txt --python .venv\Scripts\python.exe
set "ERR=1"
goto end

:deps_fail
echo [MeaPet] dependency install failed
echo manual:
echo   uv pip install -r linux_requirements.txt --python .venv\Scripts\python.exe
echo   .venv\Scripts\python.exe -m pip install -r linux_requirements.txt --prefer-binary
set "ERR=1"
goto end

:dep_check
echo [MeaPet] checking deps...
"!PY_CMD!" --version
"!PY_CMD!" -c "import PyQt5,PIL,requests,numpy,httpx,OpenGL,jieba" >nul 2>&1
if not errorlevel 1 goto ready
echo [MeaPet] installing missing deps...
call :ensure_uv
call :install_deps
if errorlevel 1 goto deps_fail

:ready
if exist "config.json" goto launch_pet
if not exist "setup_wizard.py" goto missing_wizard
echo [MeaPet] first run: setup wizard
"!PY_CMD!" setup_wizard.py
set "ERR=!ERRORLEVEL!"
if not "!ERR!"=="0" (
    echo [MeaPet] wizard exit code=!ERR!
    goto end
)
if not exist "config.json" (
    echo [MeaPet] no config.json after wizard, stop
    set "ERR=1"
    goto end
)
echo [MeaPet] config ready, starting pet...
goto launch_pet

:missing_wizard
echo [MeaPet] missing setup_wizard.py and config.json
echo copy config.example.json to config.json then retry
set "ERR=1"
goto end

:launch_pet
echo [MeaPet] start pet.py
echo [MeaPet] tray icon to quit; logs: meapet_boot.log
echo [MeaPet] debug: .venv\Scripts\python.exe -u pet.py
echo [MeaPet] --------------------------------------------
"!PY_CMD!" -u pet.py
set "ERR=!ERRORLEVEL!"
echo.
echo [MeaPet] exit code=!ERR!

if exist meapet_boot.log (
    echo [MeaPet] ---- meapet_boot.log tail ----
    powershell -NoProfile -Command "Get-Content -Path 'meapet_boot.log' -Tail 30 -ErrorAction SilentlyContinue"
)
if exist meapet_crash.log echo [MeaPet] meapet_crash.log exists
if exist meapet_fault.log (
    for %%A in (meapet_fault.log) do if %%~zA GTR 0 echo [MeaPet] meapet_fault.log non-empty
)

if not "!ERR!"=="0" (
    echo [MeaPet] abnormal exit, see meapet_crash.log
) else (
    echo [MeaPet] done
)

:end
pause
exit /b !ERR!

:create_venv_with_system_python
set "SYS_PY="
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.12 3.13 3.11 3.10 3) do (
        if not defined SYS_PY (
            py -%%V -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
            if not errorlevel 1 (
                for /f "delims=" %%i in ('py -%%V -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%i"
            )
        )
    )
)
if not defined SYS_PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%i"
        )
    )
)
if not defined SYS_PY (
    echo [MeaPet] no system Python 3.10+
    exit /b 1
)
echo [MeaPet] system Python: !SYS_PY!
"!SYS_PY!" -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [MeaPet] python -m venv failed
    exit /b 1
)
exit /b 0

:install_deps
if not defined UV_CMD call :ensure_uv
if defined UV_CMD (
    echo [MeaPet] uv pip install...
    "!UV_CMD!" pip install -r linux_requirements.txt --python "!PY_CMD!" --index-url "%UV_INDEX_URL%"
    if errorlevel 1 (
        echo [MeaPet] mirror failed, try pypi.org
        "!UV_CMD!" pip install -r linux_requirements.txt --python "!PY_CMD!" --index-url https://pypi.org/simple
    )
    if errorlevel 1 exit /b 1
    echo [MeaPet] optional live2d-py...
    "!UV_CMD!" pip install live2d-py --python "!PY_CMD!" --index-url "%UV_INDEX_URL%" >nul 2>&1
    if errorlevel 1 "!UV_CMD!" pip install live2d-py --python "!PY_CMD!" >nul 2>&1
    if errorlevel 1 echo [MeaPet] live2d-py skipped, PNG mode
    exit /b 0
)
echo [MeaPet] pip install...
"!PY_CMD!" -m pip install --upgrade pip setuptools wheel >nul 2>&1
"!PY_CMD!" -m pip install -r linux_requirements.txt --prefer-binary -i "%UV_INDEX_URL%" --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 "!PY_CMD!" -m pip install -r linux_requirements.txt --prefer-binary
if errorlevel 1 exit /b 1
"!PY_CMD!" -m pip install live2d-py --prefer-binary >nul 2>&1
if errorlevel 1 echo [MeaPet] live2d-py skipped, PNG mode
exit /b 0

:ensure_uv
set "UV_CMD="
where uv >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where uv') do (
        if not defined UV_CMD set "UV_CMD=%%i"
    )
)
if not defined UV_CMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_CMD if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV_CMD if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV_CMD=%LOCALAPPDATA%\Programs\uv\uv.exe"
if defined UV_CMD exit /b 0

echo [MeaPet] uv not found
if /I not "%MEAPET_ALLOW_DOWNLOAD%"=="1" (
    echo [MeaPet] set MEAPET_ALLOW_DOWNLOAD=1 to auto-install uv
    echo [MeaPet] will try system Python next
    exit /b 1
)

echo [MeaPet] installing uv...
powershell -ExecutionPolicy Bypass -NoProfile -Command "try { irm https://astral.sh/uv/install.ps1 | iex } catch { exit 1 }"
if errorlevel 1 (
    echo [MeaPet] uv install failed, try pip
    where python >nul 2>&1
    if not errorlevel 1 (
        python -m pip install -U uv -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
)

where uv >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where uv') do (
        if not defined UV_CMD set "UV_CMD=%%i"
    )
)
if not defined UV_CMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_CMD if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV_CMD (
    echo [MeaPet] uv ready: !UV_CMD!
    exit /b 0
)
exit /b 1
