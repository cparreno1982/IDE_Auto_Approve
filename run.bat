@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" codex_auto_approve.py
) else (
  py codex_auto_approve.py
)
if errorlevel 1 pause
