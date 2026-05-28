param(
    [string]$Source = "light_field_plugin\core\lightfield_native.c",
    [string]$Output = "light_field_plugin\core\lightfield_native.dll"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sourcePath = Join-Path $repoRoot $Source
$outputPath = Join-Path $repoRoot $Output

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Cannot find native source: $sourcePath"
}

$vcvarsCandidates = @(
    "C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
)

$vcvars = $vcvarsCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $vcvars) {
    throw "Cannot find vcvars64.bat. Install Visual Studio C++ build tools or pass a prebuilt DLL."
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null

$cmdPath = [System.IO.Path]::GetTempFileName() + ".cmd"
Set-Content -LiteralPath $cmdPath -Encoding ASCII -Value @"
@echo on
call "$vcvars"
cl /nologo /O2 /GL /LD /openmp "$sourcePath" /Fe"$outputPath" /link /LTCG
"@
try {
    cmd.exe /c "`"$cmdPath`""
    if ($LASTEXITCODE -ne 0) {
        throw "Native build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $cmdPath -Force -ErrorAction SilentlyContinue
}

$importLib = [System.IO.Path]::ChangeExtension($outputPath, ".lib")
$expFile = [System.IO.Path]::ChangeExtension($outputPath, ".exp")
Remove-Item -LiteralPath $importLib -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $expFile -Force -ErrorAction SilentlyContinue

Write-Host "Built $outputPath"
