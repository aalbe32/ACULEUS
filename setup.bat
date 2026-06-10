@echo off
setlocal

set VENV_DIR=venv

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv %VENV_DIR%
    if errorlevel 1 (
        echo Failed to create venv. Is Python installed and on PATH?
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

if exist requirements.txt (
    echo Installing requirements...
    python -m pip install -r requirements.txt
) else (
    echo No requirements.txt found, skipping install.
)



