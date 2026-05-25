param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$addonDir = Join-Path $repoRoot "light_field_plugin"
$initFile = Join-Path $addonDir "__init__.py"
$distDir = Join-Path $repoRoot "dist"

if (-not (Test-Path $initFile)) {
    throw "Cannot find add-on entrypoint: $initFile"
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $initText = Get-Content -LiteralPath $initFile -Raw
    $match = [regex]::Match($initText, '"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)')
    if (-not $match.Success) {
        throw "Cannot parse bl_info version from $initFile. Pass -Version explicitly."
    }
    $Version = "{0}.{1}.{2}" -f $match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value
}

New-Item -ItemType Directory -Force -Path $distDir | Out-Null

$zipName = "light_field_render-v$Version.zip"
$zipPath = Join-Path $distDir $zipName

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $addonDir -Recurse -File |
        Where-Object {
            $_.FullName -notmatch "\\__pycache__\\" -and
            $_.Extension -notin @(".pyc", ".pyo")
        } |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($repoRoot.Path.Length + 1)
            $entryName = $relativePath.Replace("\", "/")
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
}
finally {
    $zip.Dispose()
}

Write-Host "Created $zipPath"
