@echo off
chcp 65001 >nul
title COOLBOX - Instalar respaldo local de comprobantes
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_respaldo.ps1"
pause
