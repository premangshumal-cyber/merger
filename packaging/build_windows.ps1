$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Path "vendor\bin" -Force | Out-Null

if (-not (Test-Path "vendor\bin\ffmpeg.exe") -or -not (Test-Path "vendor\bin\ffprobe.exe")) {
    Write-Host "Downloading FFmpeg essentials binaries..."
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "vendor\ffmpeg.zip"
    Expand-Archive -Path "vendor\ffmpeg.zip" -DestinationPath "vendor\unpacked" -Force
    $ffmpeg = Get-ChildItem "vendor\unpacked" -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    $ffprobe = Get-ChildItem "vendor\unpacked" -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $ffmpeg -or -not $ffprobe) { throw "Archive is missing FFmpeg binaries" }
    Copy-Item $ffmpeg.FullName "vendor\bin\ffmpeg.exe" -Force
    Copy-Item $ffprobe.FullName "vendor\bin\ffprobe.exe" -Force
}

python -m pip install -r requirements-dev.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; stopping the build" }

$env:QT_QPA_PLATFORM = "offscreen"
python -c "from PySide6.QtWidgets import QApplication; from playlistfusion.ui import MainWindow; a=QApplication([]); w=MainWindow(); w.navigate(5); assert w.stack.currentIndex()==5; assert w.single_download_button.text()=='Download Video'; print(w.windowTitle()); w.close()"
if ($LASTEXITCODE -ne 0) { throw "GUI smoke test failed" }
Remove-Item Env:\QT_QPA_PLATFORM

$buildArgs = @("--noconfirm", "--clean", "--windowed", "--onedir", "--name", "PlaylistFusion", "--collect-all", "yt_dlp", "--add-binary", "vendor\bin\ffmpeg.exe;bin", "--add-binary", "vendor\bin\ffprobe.exe;bin", "--add-data", "LICENSE;.", "--add-data", "THIRD_PARTY_NOTICES.md;.", "--add-data", "USER_MANUAL.md;.", "launcher.py")
python -m PyInstaller @buildArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

New-Item -ItemType Directory -Path "dist\installer" -Force | Out-Null
Compress-Archive -Path "dist\PlaylistFusion\*" -DestinationPath "dist\installer\PlaylistFusion-Portable.zip" -Force

$isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$iscc = if ($isccCommand) { $isccCommand.Source } else { "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Install Inno Setup 6 to compile Setup.exe" }
& $iscc "packaging\PlaylistFusion.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Output: $root\dist\installer"
