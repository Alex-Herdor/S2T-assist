@echo off
setlocal

cd /d C:\whisperx_ru
if errorlevel 1 exit /b 1

if not exist logs mkdir logs
if not exist data\.locks mkdir data\.locks

set LOG_FILE=logs\pipeline_worker.log
set LOCK_FILE=data\.locks\pipeline_worker.lock
set CONDA_BAT=C:\Users\admin\miniconda3\condabin\conda.bat

echo [%date% %time%] Worker started >> %LOG_FILE%

if exist %LOCK_FILE% (
    echo [%date% %time%] Lock exists, another worker may be running. Exit. >> %LOG_FILE%
    exit /b 0
)

echo %date% %time% > %LOCK_FILE%

if not exist "%CONDA_BAT%" (
    echo [%date% %time%] Conda bat not found: %CONDA_BAT% >> %LOG_FILE%
    del %LOCK_FILE%
    exit /b 1
)

call "%CONDA_BAT%" activate whisperx-ru
if errorlevel 1 (
    echo [%date% %time%] Failed to activate conda env. >> %LOG_FILE%
    del %LOCK_FILE%
    exit /b 1
)

echo [%date% %time%] Conda env activated. >> %LOG_FILE%

python --version >> %LOG_FILE% 2>>&1
where python >> %LOG_FILE% 2>>&1

python scripts\pipeline.py process >> %LOG_FILE% 2>>&1
set EXIT_CODE=%ERRORLEVEL%

echo [%date% %time%] Worker finished with code %EXIT_CODE% >> %LOG_FILE%

del %LOCK_FILE%

exit /b %EXIT_CODE%