# prefetch_otp.ps1 — bulk-download BTS On-Time Performance monthly ZIPs.
#
# Toolchain-independent (PowerShell only) so acquisition can start before the
# Python environment exists. Measured BTS throughput is ~70-100 KB/s, so a
# 12-month pull is 60-90 min sequentially; we run a small number of parallel
# jobs to cut wall-clock without hammering the host.
#
# Idempotent: an already-complete file is skipped, so this can be re-run safely.

param(
    [string]$OutDir      = "C:\Users\Tomer\Desktop\brain\02-Projects\airport-investment-agent\data\raw\otp",
    [int]   $Parallel    = 3
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# Analysis window: May 2025 .. April 2026 (12 months), per approved decision.
$months = @()
foreach ($m in 5..12) { $months += ,@(2025, $m) }
foreach ($m in 1..4)  { $months += ,@(2026, $m) }

$base = "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$work = @()
foreach ($ym in $months) {
    $y = $ym[0]; $m = $ym[1]
    $work += [pscustomobject]@{
        Year = $y
        Month = $m
        Url  = "${base}_${y}_${m}.zip"
        Path = Join-Path $OutDir "otp_${y}_$('{0:D2}' -f $m).zip"
    }
}

$script:block = {
    param($Url, $Path)
    $ProgressPreference = 'SilentlyContinue'
    # Skip if we already have a valid (PK-magic, non-trivial) file.
    if (Test-Path $Path) {
        $len = (Get-Item $Path).Length
        if ($len -gt 1MB) {
            $fs = [System.IO.File]::OpenRead($Path)
            $b = New-Object byte[] 2
            $null = $fs.Read($b, 0, 2)
            $fs.Close()
            if ($b[0] -eq 0x50 -and $b[1] -eq 0x4B) {
                return "SKIP  $(Split-Path $Path -Leaf)  ($([math]::Round($len/1MB,1)) MB already present)"
            }
        }
        Remove-Item $Path -Force
    }
    $tmp = "$Path.part"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        Invoke-WebRequest -Uri $Url -OutFile $tmp -TimeoutSec 1800 -UseBasicParsing
    } catch {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
        return "FAIL  $(Split-Path $Path -Leaf)  :: $($_.Exception.Message)"
    }
    $sw.Stop()
    Move-Item $tmp $Path -Force
    $mb = (Get-Item $Path).Length / 1MB
    return ("OK    {0}  {1:N1} MB in {2:N0}s ({3:N0} KB/s)" -f (Split-Path $Path -Leaf), $mb, $sw.Elapsed.TotalSeconds, ($mb * 1024 / [Math]::Max(1, $sw.Elapsed.TotalSeconds)))
}

Write-Output "Downloading $($work.Count) OTP month-files to $OutDir (parallel=$Parallel)"
Write-Output "Window: 2025-05 .. 2026-04"
Write-Output ""

$jobs = @()
foreach ($w in $work) {
    while (@(Get-Job -State Running).Count -ge $Parallel) { Start-Sleep -Seconds 3 }
    $jobs += Start-Job -ScriptBlock $script:block -ArgumentList $w.Url, $w.Path
}

$null = Wait-Job -Job $jobs
foreach ($j in $jobs) { Receive-Job -Job $j | ForEach-Object { Write-Output $_ } }
Get-Job | Remove-Job -Force

Write-Output ""
Write-Output "=== RESULT ==="
$have = Get-ChildItem $OutDir -Filter "otp_*.zip" -ErrorAction SilentlyContinue
foreach ($f in ($have | Sort-Object Name)) {
    Write-Output ("  {0}  {1,7:N1} MB" -f $f.Name, ($f.Length / 1MB))
}
Write-Output "Files present: $($have.Count) / $($work.Count)"
