@echo off
cd /d "%~dp0"
python multiplayer.py
if errorlevel 1 pause
