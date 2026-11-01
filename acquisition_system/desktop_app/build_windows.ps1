$ErrorActionPreference = "Stop"
$AcqSourceRoot = $PSScriptRoot
$AcqProjectLocal = if ($env:ACQ_LOCAL_WORK) { $env:ACQ_LOCAL_WORK } else { Join-Path $env:LOCALAPPDATA "ADCWorkstation" }
$AcqProjectLocal = py -3.12 -c 'import os,sys; p=os.path.realpath(sys.argv[1]); assert not any(x in p.lower() for x in ("onedrive","cloudstorage","icloud","dropbox")), "Local work must be outside cloud storage"; print(p)' $AcqProjectLocal
if ($LASTEXITCODE -ne 0) { throw "请检查 Python 3.12 及非云盘本地工作根" }
$AcqLocalRoot = Join-Path $AcqProjectLocal "active\acquisition-system"
New-Item -ItemType Directory -Force -Path $AcqLocalRoot | Out-Null
Set-Location $AcqLocalRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
$AcqVenv = Join-Path $AcqLocalRoot "venv-win"
py -3.12 -m venv $AcqVenv
if ($LASTEXITCODE -ne 0) { throw "请先安装 Windows Python 3.12" }
$AcqPython = Join-Path $AcqVenv "Scripts\python.exe"
& $AcqPython -m pip install -r (Join-Path $AcqSourceRoot "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
& $AcqPython -m PyInstaller --noconfirm --clean --windowed --onedir --name ADCWorkstation --paths (Split-Path $AcqSourceRoot -Parent) --workpath (Join-Path $AcqLocalRoot "build") --distpath (Join-Path $AcqLocalRoot "dist") --specpath $AcqLocalRoot --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets (Join-Path $AcqSourceRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "打包失败" }
Write-Host "输出：$AcqLocalRoot\dist\ADCWorkstation\ADCWorkstation.exe"
Write-Host "请完整分发 ADCWorkstation 文件夹，并按 README 在目标 Windows 机器验收。"
