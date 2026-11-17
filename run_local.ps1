# Запуск из исходников с локальной папкой libs (там лежат wheels, скачанные fetch_deps.py).
#
#   .\run_local.ps1                       # GUI
#   .\run_local.ps1 --mock                # демо без устройства
#   .\run_local.ps1 --check --host 192.168.1.20 --user manager --password "***"
#
param([Parameter(ValueFromRemainingArguments = $true)]$Rest)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (Test-Path "libs\numpy") {
    $env:PYTHONPATH = "$root\libs"
} else {
    Write-Host "libs не найдены — используйте обычный pip install -r requirements.txt"
}

$env:PYTHONIOENCODING = "utf-8"
python run_airview.py @Rest
