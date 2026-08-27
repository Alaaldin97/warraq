# Build the Warraq shell with the MSVC toolchain.
#
# vcvars64.bat depends on vswhere.exe, which the Build Tools installer does not
# always place. This script sets the compiler environment directly from the
# known install locations instead, so the build works on a fresh machine.
#
#   .\build.ps1            compile-check
#   .\build.ps1 dev        run the app in development
#   .\build.ps1 release    produce a release build

param([string]$Task = "check")

$ErrorActionPreference = "Stop"

$vsRoot = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools"
$msvcRoot = Join-Path $vsRoot "VC\Tools\MSVC"
$msvcVer = (Get-ChildItem $msvcRoot -Directory | Sort-Object Name -Descending |
            Select-Object -First 1).Name
$sdkRoot = "${env:ProgramFiles(x86)}\Windows Kits\10"
$sdkVer = (Get-ChildItem "$sdkRoot\Include" -Directory |
           Sort-Object Name -Descending | Select-Object -First 1).Name

Write-Host "MSVC $msvcVer · Windows SDK $sdkVer" -ForegroundColor Cyan

$msvc = Join-Path $msvcRoot $msvcVer

$env:PATH = @(
  "$msvc\bin\Hostx64\x64"
  "$sdkRoot\bin\$sdkVer\x64"
  "$env:USERPROFILE\.cargo\bin"
  $env:PATH
) -join ";"

$env:LIB = @(
  "$msvc\lib\x64"
  "$sdkRoot\Lib\$sdkVer\ucrt\x64"
  "$sdkRoot\Lib\$sdkVer\um\x64"
) -join ";"

$env:INCLUDE = @(
  "$msvc\include"
  "$sdkRoot\Include\$sdkVer\ucrt"
  "$sdkRoot\Include\$sdkVer\um"
  "$sdkRoot\Include\$sdkVer\shared"
  "$sdkRoot\Include\$sdkVer\winrt"
) -join ";"

Push-Location $PSScriptRoot
try {
    switch ($Task) {
        "check"   { Push-Location src-tauri; cargo check; Pop-Location }
        "dev"     { npm run tauri dev }
        "release" { npm run tauri build }
        default   { throw "unknown task '$Task' (use check, dev or release)" }
    }
} finally {
    Pop-Location
}
