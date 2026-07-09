@echo off
cd /d "%~dp0"
title MeaPet

set PY_CMD=
python --version >nul 2>&1
if %errorlevel% equ 0 set PY_CMD=python&goto py_found
py --version >nul 2>&1
if %errorlevel% equ 0 set PY_CMD=py&goto py_found
for %%v in (313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" set PY_CMD="%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"&goto py_found
    if exist "%ProgramFiles%\Python\Python%%v\python.exe" set PY_CMD="%ProgramFiles%\Python\Python%%v\python.exe"&goto py_found
)
echo Python not found. Download from python.org
pause
exit /b 1

:py_found
%PY_CMD% --version
echo.

%PY_CMD% -c "import PyQt5" 2>nul
if errorlevel 1 %PY_CMD% -m pip install PyQt5 pywin32 requests pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 echo Install failed && pause && exit /b 1

%PY_CMD% -c "import live2d.v3" 2>nul
if errorlevel 1 %PY_CMD% -m pip install live2d-py PyOpenGL -i https://pypi.tuna.tsinghua.edu.cn/simple

%PY_CMD% -c "import soundfile" 2>nul
if errorlevel 1 (
    %PY_CMD% -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    %PY_CMD% -m pip install soundfile numpy einops av PyYAML tqdm pypinyin scipy psutil -i https://pypi.tuna.tsinghua.edu.cn/simple
    %PY_CMD% -m pip install transformers tokenizers huggingface_hub -i https://pypi.tuna.tsinghua.edu.cn/simple
)

if exist "GPT-SoVITS-CPUFast/requirements.txt" (
    %PY_CMD% -c "import gradio" 2>nul
    if errorlevel 1 %PY_CMD% -m pip install -r "GPT-SoVITS-CPUFast/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
)

if exist "config.json" goto run
%PY_CMD% setup_wizard.py
%PY_CMD% precache_interactions.py
goto end

:run
%PY_CMD% pet.py

:end
pause
