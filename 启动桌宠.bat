@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title MeaPet

set PY_CMD=

:: ======== 1. 检测已有 Python ========

:: 0. Hermes venv（自带 PyTorch，免装依赖）
if exist "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" (
    set PY_CMD="%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
    goto dep_check
)
if exist "%USERPROFILE%\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" (
    set PY_CMD="%USERPROFILE%\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
    goto dep_check
)

:: 1a. 系统 PATH
python --version >nul 2>&1
if %errorlevel% equ 0 set PY_CMD=python&goto dep_check
py --version >nul 2>&1
if %errorlevel% equ 0 set PY_CMD=py&goto dep_check

:: 1b. 常见安装路径
for %%v in (313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set PY_CMD="%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"&goto dep_check
    if exist "%ProgramFiles%\Python\Python%%v\python.exe" set PY_CMD="%ProgramFiles%\Python\Python%%v\python.exe"&goto dep_check
)

:: 1c. 便携版（有 pip 才直接用）
if exist "%~dp0_python\python.exe" set PY_CMD="%~dp0_python\python.exe"
if defined PY_CMD %PY_CMD% -m pip --version >nul 2>&1
if defined PY_CMD if not errorlevel 1 goto dep_check
if defined PY_CMD echo [MeaPet] 发现 _python\ 但 pip 未就绪，重新安装 pip ...

:: ======== 2. 需要下载/修复 python + pip ========

:: 没 PY_CMD 则下载 embeddable
if defined PY_CMD goto fix_pip

echo [MeaPet] 未检测到 Python，正在下载 Python 3.11（约 11MB）...

set PS_SCRIPT=%TEMP%\meapet_dl_python.ps1
> "%PS_SCRIPT%" echo $url = 'https://mirrors.tuna.tsinghua.edu.cn/python/3.11.9/python-3.11.9-embed-amd64.zip'
>> "%PS_SCRIPT%" echo $fbk = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'
>> "%PS_SCRIPT%" echo $dir = Join-Path $pwd '_python'
>> "%PS_SCRIPT%" echo $zip = Join-Path $pwd '_python.zip'
>> "%PS_SCRIPT%" echo write-host '  downloading ...'
>> "%PS_SCRIPT%" echo [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
>> "%PS_SCRIPT%" echo try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing }
>> "%PS_SCRIPT%" echo catch { write-host '  mirror fallback ...'; Invoke-WebRequest -Uri $fbk -OutFile $zip -UseBasicParsing }
>> "%PS_SCRIPT%" echo if (-not (Test-Path $zip^)^) { write-host '  download FAILED'; exit 1 }
>> "%PS_SCRIPT%" echo write-host '  extracting ...'
>> "%PS_SCRIPT%" echo Expand-Archive -Path $zip -DestinationPath $dir -Force
>> "%PS_SCRIPT%" echo Remove-Item $zip
powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
if %errorlevel% neq 0 (
    echo [MeaPet] Python 下载失败
    echo 请手动安装 Python 3.11：https://www.python.org/downloads/
    pause
    exit /b 1
)
del "%PS_SCRIPT%"

if not exist "%~dp0_python\python.exe" (
    echo [MeaPet] Python 解压失败
    pause
    exit /b 1
)
set PY_CMD="%~dp0_python\python.exe"

:: ======== 3. 安装 pip ========
:fix_pip
echo [MeaPet] 正在安装 pip ...

set PS_SCRIPT=%TEMP%\meapet_dl_python.ps1
> "%PS_SCRIPT%" echo $py = '%PY_CMD:"=%'
>> "%PS_SCRIPT%" echo $dir = Join-Path $pwd '_python'
>> "%PS_SCRIPT%" echo write-host '  enabling site-packages ...'
>> "%PS_SCRIPT%" echo $pth = Get-ChildItem $dir -Filter '*._pth' ^| Select-Object -First 1 -ExpandProperty FullName
>> "%PS_SCRIPT%" echo if ($pth^) { (Get-Content $pth -Raw^) -replace '#import site', 'import site' ^| Set-Content $pth }
>> "%PS_SCRIPT%" echo write-host '  downloading get-pip.py ...'
>> "%PS_SCRIPT%" echo $gp = Join-Path $dir 'get-pip.py'
>> "%PS_SCRIPT%" echo $ok = $false
>> "%PS_SCRIPT%" echo $urls = @('https://bootstrap.pypa.io/get-pip.py','https://raw.githubusercontent.com/pypa/get-pip/main/public/get-pip.py')
>> "%PS_SCRIPT%" echo foreach ($u in $urls^) { try { Invoke-WebRequest -Uri $u -OutFile $gp -UseBasicParsing; $ok = $true; break } catch { write-host ('    ' + $u + ' failed'^) } }
>> "%PS_SCRIPT%" echo if (-not $ok^) { write-host '  get-pip.py download FAILED'; exit 1 }
>> "%PS_SCRIPT%" echo write-host '  running get-pip.py ...'
>> "%PS_SCRIPT%" echo ^& $py $gp
>> "%PS_SCRIPT%" echo if ($LASTEXITCODE -ne 0^) { write-host '  pip install FAILED'; exit 1 }
>> "%PS_SCRIPT%" echo Remove-Item $gp
>> "%PS_SCRIPT%" echo write-host '  verifying pip ...'
>> "%PS_SCRIPT%" echo ^& $py -m pip --version 2^>$null
>> "%PS_SCRIPT%" echo if ($LASTEXITCODE -ne 0^) { write-host '  pip verify FAILED'; exit 1 }
>> "%PS_SCRIPT%" echo write-host '  pip ready!'
powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set PIP_EXIT=%errorlevel%
del "%PS_SCRIPT%"

:: 备用 ensurepip
if %PIP_EXIT% neq 0 (
    echo [MeaPet] get-pip.py 失败，尝试 ensurepip ...
    %PY_CMD% -m ensurepip --upgrade >nul 2>&1
    %PY_CMD% -m pip --version >nul 2>&1
    if not errorlevel 1 set PIP_EXIT=0
)

if %PIP_EXIT% neq 0 (
    echo [MeaPet] pip 安装失败
    echo 请手动运行：%PY_CMD% -m ensurepip --upgrade
    pause
    exit /b 1
)

echo [MeaPet] Python 3.11 + pip 已就绪！

:: ======== 4. 安装基础依赖 ========
:dep_check
%PY_CMD% --version

echo [MeaPet] 正在安装基础依赖（PyQt5, pillow, PyOpenGL, numpy）...
%PY_CMD% -m pip install -r linux_requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if %errorlevel% neq 0 (
    echo [MeaPet] 基础依赖安装失败喵
    pause
    exit /b 1
)

echo [MeaPet] 正在安装 Live2D 支持（live2d-py）...
%PY_CMD% -m pip install live2d-py --index-url https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if %errorlevel% neq 0 (
    echo [MeaPet] Live2D 支持安装失败，桌宠将以 PNG 模式运行喵
)

:: ======== 5. 启动 ========
if not exist "config.json" (
    %PY_CMD% setup_wizard.py
    goto end
)

%PY_CMD% pet.py

:end
pause
