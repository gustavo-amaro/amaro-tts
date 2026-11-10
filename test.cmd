@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test.ps1" %*
