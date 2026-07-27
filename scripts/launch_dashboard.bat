@echo off
cd /d "%~dp0.."

if not exist "venv\Scripts\activate.bat" (
    echo Virtual environment not found at venv\Scripts\activate.bat
    echo Run: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Starting Roman Job Radar dashboard...
echo Your browser will open automatically. Close this window to stop the dashboard.
streamlit run app.py

pause
