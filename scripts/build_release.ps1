param(
    [string]$Version = "",
    [string]$BlenderExe = "",
    [switch]$NoBundleNumpy
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

function Find-BlenderExe {
    if (-not [string]::IsNullOrWhiteSpace($BlenderExe) -and (Test-Path -LiteralPath $BlenderExe)) {
        return (Resolve-Path -LiteralPath $BlenderExe).Path
    }

    $command = Get-Command blender -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "D:\Program Files (x86)\Blender\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        "C:\Program Files\Blender Foundation\Blender\blender.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return ""
}

function Add-DirectoryToZip {
    param(
        [System.IO.Compression.ZipArchive]$Zip,
        [string]$SourceDir,
        [string]$EntryRoot
    )

    if (-not (Test-Path -LiteralPath $SourceDir)) {
        return
    }

    $resolved = Resolve-Path -LiteralPath $SourceDir
    Get-ChildItem -LiteralPath $resolved.Path -Recurse -File |
        Where-Object {
            $_.FullName -notmatch "\\__pycache__\\" -and
            $_.Extension -notin @(".pyc", ".pyo")
        } |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($resolved.Path.Length + 1)
            $entryName = ($EntryRoot.TrimEnd("/") + "/" + $relativePath).Replace("\", "/")
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $Zip,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
}

function Get-BlenderNumpyBundlePaths {
    $exe = Find-BlenderExe
    if ([string]::IsNullOrWhiteSpace($exe)) {
        Write-Warning "Cannot find Blender executable; release ZIP will rely on Blender's installed NumPy."
        return $null
    }

    $scriptPath = [System.IO.Path]::GetTempFileName() + ".py"
    $script = @"
import json
import pathlib
import numpy
root = pathlib.Path(numpy.__file__).resolve().parent
parent = root.parent
payload = {
    "numpy": str(root),
    "metadata": [str(path) for path in list(parent.glob("numpy*.dist-info")) + list(parent.glob("numpy*.egg-info"))]
}
print("NUMPY_BUNDLE_JSON=" + json.dumps(payload))
"@
    Set-Content -LiteralPath $scriptPath -Value $script -Encoding UTF8
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $exe --background --python $scriptPath 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
        Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0) {
        Write-Warning "Blender NumPy discovery failed; release ZIP will rely on Blender's installed NumPy."
        Write-Warning ($output -join "`n")
        return $null
    }

    $line = $output | Where-Object { $_ -like "NUMPY_BUNDLE_JSON=*" } | Select-Object -Last 1
    if (-not $line) {
        Write-Warning "Could not parse Blender NumPy location; release ZIP will rely on Blender's installed NumPy."
        return $null
    }
    return ($line.Substring("NUMPY_BUNDLE_JSON=".Length) | ConvertFrom-Json)
}

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
    if (-not $NoBundleNumpy) {
        $numpyBundle = Get-BlenderNumpyBundlePaths
        if ($numpyBundle -and (Test-Path -LiteralPath $numpyBundle.numpy)) {
            Add-DirectoryToZip -Zip $zip -SourceDir $numpyBundle.numpy -EntryRoot "light_field_plugin/_vendor/numpy"
            foreach ($metadataDir in @($numpyBundle.metadata)) {
                if (Test-Path -LiteralPath $metadataDir) {
                    $leaf = Split-Path -Leaf $metadataDir
                    Add-DirectoryToZip -Zip $zip -SourceDir $metadataDir -EntryRoot "light_field_plugin/_vendor/$leaf"
                }
            }
            Write-Host "Bundled NumPy from $($numpyBundle.numpy)"
        }
    }
}
finally {
    $zip.Dispose()
}

Write-Host "Created $zipPath"
