# Gera tudo em .\build :
#   BatteryHub-Setup-<versão>.exe   instalador do Windows (tema escuro, escolha da pasta)
#   BatteryHub-<versão>.apk         app Android de release, assinado
#   SHA256SUMS.txt                  somas de verificação
#
# Requisitos: Python 3.10+ com `pip install -r pc\requirements.txt`, JDK 17 e Android SDK.
# JAVA_HOME / ANDROID_HOME são lidos do ambiente; se vazios, usa %USERPROFILE%\dev-tools\jdk17 e \android-sdk.
# Os programas nativos (PyInstaller, Gradle) escrevem logs em stderr; o resultado é checado por Check().
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$root = $PSScriptRoot
$work = Join-Path $root ".build-work"
$out = Join-Path $root "build"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what falhou (código $LASTEXITCODE)" } }

$version = (Select-String -Path "$root\installer\installer.py" -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value

Step "Preparando pastas (versão $version)"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work, $out | Out-Null
Get-ChildItem $out -File | Remove-Item -Force

Step "Gerando ícone"
python "$root\tools\make_icon.py"; Check "Ícone"
$icon = "$root\assets\battery_hub.ico"

Step "Compilando o aplicativo do Windows (PyInstaller)"
python -m PyInstaller --noconfirm --clean --noconsole --onedir --name BatteryHub `
    --icon $icon --hidden-import pystray._win32 --exclude-module numpy `
    --distpath "$work\dist" --workpath "$work\pyi-app" --specpath $work `
    "$root\pc\run.py"
Check "PyInstaller (app)"

Step "Empacotando os arquivos do instalador"
python -c @"
import sys, zipfile
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in sorted(src.rglob('*')):
        if f.is_file():
            z.write(f, f.relative_to(src).as_posix())
print(dest, round(dest.stat().st_size / 1048576, 1), 'MB')
"@ "$work\dist\BatteryHub" "$work\payload.zip"
Check "Pacote"

Step "Compilando o instalador (PyInstaller)"
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name "BatteryHub-Setup" `
    --icon $icon --exclude-module numpy --paths "$root\pc" --add-data "$work\payload.zip;." `
    --distpath "$work\dist-setup" --workpath "$work\pyi-setup" --specpath $work `
    "$root\installer\installer.py"
Check "PyInstaller (instalador)"
Copy-Item "$work\dist-setup\BatteryHub-Setup.exe" "$out\BatteryHub-Setup-$version.exe"

Step "Compilando o APK de release (Gradle)"
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = "$env:USERPROFILE\dev-tools\jdk17" }
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:USERPROFILE\dev-tools\android-sdk" }
Push-Location "$root\android"
try { .\gradlew.bat assembleRelease --no-daemon -q; Check "Gradle" } finally { Pop-Location }
Copy-Item "$root\android\app\build\outputs\apk\release\app-release.apk" "$out\BatteryHub-$version.apk"

Step "Somas de verificação"
$sums = Get-ChildItem $out -File | Where-Object Name -ne "SHA256SUMS.txt" | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
}
# LF (não CRLF) para o arquivo funcionar com `sha256sum -c` em qualquer sistema
[IO.File]::WriteAllText("$out\SHA256SUMS.txt", (($sums -join "`n") + "`n"), [Text.Encoding]::ASCII)

Step "Pronto"
Get-ChildItem $out | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 2) } } | Format-Table -AutoSize
