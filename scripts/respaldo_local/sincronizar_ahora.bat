@echo off
chcp 65001 >nul
title COOLBOX - Respaldo de comprobantes
if "%1"=="verificar" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "C:\COOLBOX\respaldo\respaldo_comprobantes.ps1" -Verificar
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "C:\COOLBOX\respaldo\respaldo_comprobantes.ps1"
)
start "" "C:\COOLBOX\comprobantes"
pause
