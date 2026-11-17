@echo off
rem Сборка exe без проблем с ExecutionPolicy:
rem   build.bat            - папка dist\AirViewAnalyzer
rem   build.bat -OneFile   - один exe
rem   build.bat -Console   - консольная сборка (для диагностики)
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 (
    echo.
    echo Сборка завершилась с ошибкой. Смотрите вывод выше.
    pause
)
