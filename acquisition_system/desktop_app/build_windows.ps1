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

$AcqDriverRoot = Join-Path $AcqSourceRoot "drivers\arduino-mega2560-official"
$AcqDriverHashes = @{
    "arduino.inf" = "b0a328e4c091c99bb5383f6ed1b1bb4ffb7294290a3d387dddbb1921cca6ffc3"
    "arduino.cat" = "25f58519bc9ee9968607d8f0d8375f4736d30b17ff612d43c806b03f9460b4e3"
}
function Test-AcqDriverBundle([string] $DriverRoot) {
    foreach ($Entry in $AcqDriverHashes.GetEnumerator()) {
        $DriverFile = Join-Path $DriverRoot $Entry.Key
        if (-not (Test-Path -LiteralPath $DriverFile -PathType Leaf)) {
            throw "驱动资源缺失：$($Entry.Key)"
        }
        $ActualHash = (Get-FileHash -LiteralPath $DriverFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualHash -ne $Entry.Value) {
            throw "驱动资源校验失败：$($Entry.Key)"
        }
    }
}
Test-AcqDriverBundle $AcqDriverRoot
foreach ($CatalogName in @("arduino.cat")) {
    $Signature = Get-AuthenticodeSignature -FilePath (Join-Path $AcqDriverRoot $CatalogName)
    if ($Signature.Status -ne "Valid") {
        throw "Arduino 驱动签名校验失败：$CatalogName ($($Signature.Status))"
    }
}

$AcqDistRoot = Join-Path $AcqLocalRoot "dist"
& $AcqPython -m PyInstaller --noconfirm --clean --windowed --onedir --name ShearAcquisition --paths (Split-Path $AcqSourceRoot -Parent) --workpath (Join-Path $AcqLocalRoot "build") --distpath $AcqDistRoot --specpath $AcqLocalRoot --add-data "$($AcqSourceRoot)\drivers;drivers" --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets (Join-Path $AcqSourceRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "打包失败" }
$AcqPackagedDrivers = Join-Path $AcqDistRoot "ShearAcquisition\_internal\drivers\arduino-mega2560-official"
Test-AcqDriverBundle $AcqPackagedDrivers

$AcqArchive = Join-Path $AcqLocalRoot "ShearAcquisition-1.2.6-windows-x64.zip"
if (Test-Path -LiteralPath $AcqArchive) { Remove-Item -LiteralPath $AcqArchive -Force }
Compress-Archive -Path (Join-Path $AcqDistRoot "ShearAcquisition") -DestinationPath $AcqArchive
Write-Host "输出：$AcqArchive"
Write-Host "请完整解压 ShearAcquisition 文件夹。仅在 Windows 明确识别到支持的设备缺少驱动时，软件才会请求系统授权。"
