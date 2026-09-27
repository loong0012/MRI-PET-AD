@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  AD-Screen 后端一键启动脚本（Windows）
REM ------------------------------------------------------------------
REM  解决痛点：系统 Python 3.13（Microsoft Store 版）自带的 MINGW numpy
REM  会间歇性 access violation 崩溃（退出码 3221225477），导致
REM  `python -c "from main import app"` / uvicorn 直接挂掉。
REM  本脚本自动挑选「能正常 import numpy」的 Python 解释器启动服务：
REM    1) 环境变量 ADSCREEN_PYTHON 指定的解释器（最高优先级）
REM    2) Anaconda / Miniconda 常见安装路径（numpy 为稳定官方构建）
REM    3) PATH 中的 python（先做 numpy 可用性探测，崩溃则跳过）
REM  可选环境变量：
REM    ADSCREEN_PORT   监听端口（默认 8000）
REM    ADSCREEN_HOST   监听地址（默认 0.0.0.0）
REM ============================================================

cd /d "%~dp0"

if "%ADSCREEN_PORT%"=="" set ADSCREEN_PORT=8000
if "%ADSCREEN_HOST%"=="" set ADSCREEN_HOST=0.0.0.0

set "PY_EXE="

REM ---- 1) 显式指定的解释器 ----
if not "%ADSCREEN_PYTHON%"=="" (
    if exist "%ADSCREEN_PYTHON%" set "PY_EXE=%ADSCREEN_PYTHON%"
)

REM ---- 2) 常见 Anaconda / Miniconda 路径 ----
if "%PY_EXE%"=="" (
    for %%P in (
        "%USERPROFILE%\anaconda3\python.exe"
        "%USERPROFILE%\miniconda3\python.exe"
        "%USERPROFILE%\AppData\Local\anaconda3\python.exe"
        "%USERPROFILE%\AppData\Local\miniconda3\python.exe"
        "C:\ProgramData\Anaconda3\python.exe"
        "C:\ProgramData\miniconda3\python.exe"
        "E:\Anaconda\python.exe"
        "D:\Anaconda\python.exe"
    ) do (
        if exist %%~P if "%PY_EXE%"=="" set "PY_EXE=%%~P"
    )
)

REM ---- 3) PATH 中的 python（需通过 numpy 稳定性探测） ----
if "%PY_EXE%"=="" (
    echo [run] 未找到 Anaconda/Miniconda，尝试探测 PATH 中的 python...
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if "%PY_EXE%"=="" (
            echo [run] 探测 %%I ...
            "%%I" -c "import numpy; numpy.zeros(4)" >nul 2>&1
            if !errorlevel! equ 0 (
                set "PY_EXE=%%I"
            ) else (
                echo [run]   该解释器 numpy 不可用（MINGW 崩溃风险），跳过
            )
        )
    )
)

if "%PY_EXE%"=="" (
    echo [run] 错误：未找到可用的 Python 解释器。
    echo        请安装 Anaconda/Miniconda，或设置环境变量 ADSCREEN_PYTHON 指向 python.exe
    pause
    exit /b 1
)

echo [run] 使用 Python：%PY_EXE%
"%PY_EXE%" -c "import sys; print('[run] 版本:', sys.version.split()[0])"

REM ---- 4) 依赖快速自检（缺依赖给出明确提示而非裸崩溃） ----
"%PY_EXE%" -c "import fastapi, uvicorn, sqlalchemy, jose, bcrypt" >nul 2>&1
if not %errorlevel% equ 0 (
    echo [run] 依赖不完整，正在尝试：%PY_EXE% -m pip install -r requirements.txt
    "%PY_EXE%" -m pip install -r requirements.txt
)

echo [run] 启动 uvicorn：http://%ADSCREEN_HOST%:%ADSCREEN_PORT%
"%PY_EXE%" -m uvicorn main:app --host %ADSCREEN_HOST% --port %ADSCREEN_PORT%

endlocal
