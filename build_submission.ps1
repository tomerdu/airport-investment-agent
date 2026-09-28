# build_submission.ps1 — assemble the Deloitte submission package.
#
# Uses an explicit ALLOW-list rather than an exclude-list: anything not named
# here does not ship. That way a stray artefact added later cannot leak in by
# default, and secrets cannot be forgotten.
#
#   powershell -File build_submission.ps1
#   powershell -File build_submission.ps1 -OutDir "D:\somewhere"

param(
    [string]$OutDir = "$PSScriptRoot\..\submission"
)

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$name = "airport-investment-agent"
$stage = Join-Path $OutDir $name

Write-Output "Source : $src"
Write-Output "Staging: $stage`n"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# --- files that ship --------------------------------------------------------
$files = @(
    'README.md',
    'DEMO.md',
    '.env.example',
    '.gitignore'
)

# --- directories that ship, with per-directory exclusions -------------------
$dirs = @(
    @{ Path = 'docs';             Exclude = @() },
    @{ Path = 'backend\app';      Exclude = @('__pycache__') },
    @{ Path = 'backend\etl';      Exclude = @('__pycache__') },
    @{ Path = 'backend\tests';    Exclude = @('__pycache__', '.pytest_cache') },
    @{ Path = 'frontend\src';     Exclude = @('assets') },
    @{ Path = 'frontend\public';  Exclude = @() }
)

$rootScripts = @(
    'backend\requirements.txt',
    'backend\pytest.ini',
    'backend\smoke_test.py',
    'backend\measure_tokens.py',
    'backend\report_checkpoint2.py',
    'backend\demo_phase3.py',
    'frontend\package.json',
    'frontend\package-lock.json',
    'frontend\tsconfig.json',
    'frontend\tsconfig.app.json',
    'frontend\tsconfig.node.json',
    'frontend\vite.config.ts',
    'frontend\index.html',
    'frontend\.oxlintrc.json'
)

foreach ($f in $files + $rootScripts) {
    $from = Join-Path $src $f
    if (-not (Test-Path $from)) { Write-Warning "missing: $f"; continue }
    $to = Join-Path $stage $f
    New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null
    Copy-Item $from $to
    Write-Output "  file  $f"
}

foreach ($d in $dirs) {
    $from = Join-Path $src $d.Path
    if (-not (Test-Path $from)) { Write-Warning "missing: $($d.Path)"; continue }
    $to = Join-Path $stage $d.Path
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    Copy-Item "$from\*" $to -Recurse -Force
    foreach ($ex in $d.Exclude) {
        Get-ChildItem $to -Recurse -Force -Filter $ex -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Write-Output "  dir   $($d.Path)"
}

# --- the warehouse: required at runtime, so it ships ------------------------
$wh = Join-Path $src 'backend\app\data\warehouse.db'
$whOut = Join-Path $stage 'backend\app\data\warehouse.db'
New-Item -ItemType Directory -Force -Path (Split-Path $whOut) | Out-Null
Copy-Item $wh $whOut
Write-Output ("  data  backend\app\data\warehouse.db  ({0:N1} MB)" -f ((Get-Item $wh).Length / 1MB))

# Drop any WAL sidecars that tagged along.
Get-ChildItem $stage -Recurse -Include *.db-wal, *.db-shm -Force -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# --- safety net: refuse to produce a package containing a secret ------------
Write-Output "`nSecurity scan..."
$leaks = Get-ChildItem $stage -Recurse -File -Force |
    Where-Object { $_.Extension -notin '.db', '.zip', '.png', '.jpg' } |
    Select-String -Pattern 'sk-ant-[A-Za-z0-9_\-]{20,}' -List
if ($leaks) {
    $leaks | ForEach-Object { Write-Error "SECRET IN PACKAGE: $($_.Path)" }
    throw "Aborting: the package contains what looks like an API key."
}
if (Test-Path (Join-Path $stage '.env')) { throw "Aborting: .env was staged." }
Write-Output "  no API keys found; no .env staged"

# --- archive ----------------------------------------------------------------
$zip = Join-Path $OutDir "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal

$fileCount = (Get-ChildItem $stage -Recurse -File -Force).Count
$stageSize = (Get-ChildItem $stage -Recurse -File -Force | Measure-Object Length -Sum).Sum

Write-Output "`n=== PACKAGE BUILT ==="
Write-Output ("  folder : {0}" -f $stage)
Write-Output ("  archive: {0}  ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
Write-Output ("  files  : {0}   unpacked {1:N1} MB" -f $fileCount, ($stageSize / 1MB))
