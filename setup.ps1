$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
& '.\.venv\Scripts\python.exe' configure.py
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить настройки.' }
Write-Host 'Теперь заполните .env и запустите run_sketch.py и run_bot.py в двух терминалах.'
