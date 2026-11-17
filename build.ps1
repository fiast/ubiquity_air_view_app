# Сборка airView Analyzer в exe (PyInstaller).
#
#   .\build.ps1              # папка dist\AirViewAnalyzer (рекомендуется)
#   .\build.ps1 -OneFile     # один exe (медленнее стартует, распаковывается во временную папку)
#   .\build.ps1 -Console     # консольная сборка (для диагностики, видны логи)
#
# Если PowerShell не даёт запустить скрипт (ExecutionPolicy), используйте build.bat —
# он запускает этот же файл с -ExecutionPolicy Bypass.
param(
    [switch]$OneFile,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Python: $((Get-Command python).Source)"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
$window = if ($Console) { "--console" } else { "--windowed" }
$name = if ($Console) { "AirViewAnalyzerConsole" } else { "AirViewAnalyzer" }

python -m PyInstaller `
    --noconfirm --clean $window `
    $mode `
    --name $name `
    --paths "$root" `
    --collect-all pyqtgraph `
    --hidden-import PySide6.QtOpenGL `
    --hidden-import PySide6.QtOpenGLWidgets `
    run_airview.py

$target = if ($OneFile) { "dist\$name.exe" } else { "dist\$name\$name.exe" }
Write-Host ""
Write-Host "Готово. Запуск: $target"
