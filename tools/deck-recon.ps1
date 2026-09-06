# deck-recon.ps1 -- run deck/recon.sh on the Dudu7 over network ADB, save the output.
#
#   .\tools\deck-recon.ps1 -DeckIp 192.168.1.45
#   .\tools\deck-recon.ps1 -DeckIp 192.168.1.45 -Port 5555
#
# Prereqs: laptop and deck on the same WiFi; USB debugging enabled on the
# deck (Settings -> tap "System" 4x -> password 3368 -> Developer Options,
# see deck/bootstrap.md step 1); adb (platform-tools) on PATH.  FYT units
# have host-only USB ports -- no gadget mode -- so network ADB is the ONLY
# adb route; there is no USB fallback to try.
#
# The script is pushed to /data/local/tmp and run there rather than piped
# through stdin, because adb's stdin path mangles line endings unpredictably
# across adb versions; the push route also leaves the script on the deck for
# manual re-runs.  CRLF is stripped before pushing since Android's sh
# treats a bare \r as part of the token.
#
# Output: docs\recon-<date>.txt plus a printed summary of the VERDICT lines
# (su, ttyACM, CIRCUITPY, phantom-killer flags).

param(
    [Parameter(Mandatory = $true)]
    [string]$DeckIp,
    [int]$Port = 5555
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$reconSrc = Join-Path $repo "deck\recon.sh"
if (-not (Test-Path $reconSrc)) {
    Write-Output "missing $reconSrc"
    exit 1
}
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Write-Output "adb not found on PATH -- install Android platform-tools"
    exit 1
}

$target = "${DeckIp}:${Port}"
Write-Output "connecting to $target"
$conn = & adb connect $target
Write-Output $conn
if (-not ($conn -match "connected")) {
    Write-Output "adb connect failed. Is USB debugging on (dev options, code 3368)"
    Write-Output "and the deck on this network? Try: ping $DeckIp"
    exit 1
}

# Quick liveness check -- 'connected' can be reported for a half-dead link.
$probe = & adb -s $target shell echo ok
if ($LASTEXITCODE -ne 0 -or -not ($probe -match "ok")) {
    Write-Output "adb shell not responding on $target"
    exit 1
}

# Normalize line endings and push.
$raw = [IO.File]::ReadAllText($reconSrc).Replace("`r`n", "`n")
$tmp = Join-Path $env:TEMP "scirocco-recon.sh"
[IO.File]::WriteAllText($tmp, $raw, (New-Object System.Text.UTF8Encoding($false)))

& adb -s $target push $tmp /data/local/tmp/scirocco-recon.sh | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Output "adb push failed; falling back to stdin (output identical)"
    $out = cmd /c "adb -s $target shell sh < `"$tmp`""
} else {
    Write-Output "running recon (su probe may need a tap on the deck screen)..."
    $out = & adb -s $target shell sh /data/local/tmp/scirocco-recon.sh
}
if (-not $out) {
    Write-Output "no output from the deck; run the script by hand:"
    Write-Output "  adb -s $target shell sh /data/local/tmp/scirocco-recon.sh"
    exit 1
}

# adb on Windows historically emits CRLF; trim so saved file and matches are clean.
$out = @($out) | ForEach-Object { "$_".TrimEnd() }

$date = Get-Date -Format "yyyy-MM-dd"
$outPath = Join-Path $repo "docs\recon-$date.txt"
if (Test-Path $outPath) {
    $outPath = Join-Path $repo ("docs\recon-$date" + "_" + (Get-Date -Format "HHmmss") + ".txt")
}
$out | Out-File -FilePath $outPath -Encoding utf8
Write-Output ""
Write-Output "full output saved to $outPath"

$verdicts = $out | Where-Object { $_ -match "^VERDICT " }

function Get-Verdict([string]$key) {
    foreach ($line in $verdicts) {
        if ($line -match "^VERDICT $key=(.*)$") { return $Matches[1] }
    }
    return "?"
}

Write-Output ""
Write-Output "=== key verdicts ==="
if (-not $verdicts) {
    Write-Output "  no VERDICT lines found -- the script died early; read the full output"
    exit 1
}
Write-Output ("  android:   " + (Get-Verdict "android_release") + " on " + (Get-Verdict "platform"))
Write-Output ("  su:        " + (Get-Verdict "su"))
Write-Output ("  selinux:   " + (Get-Verdict "selinux"))
Write-Output ("  ttyACM:    " + (Get-Verdict "ttyacm") + "  (no = board unplugged, or kernel lacks cdc_acm)")
Write-Output ("  CIRCUITPY: " + (Get-Verdict "circuitpy"))
Write-Output ("  phantom monitor flag:  " + (Get-Verdict "phantom_monitor") + "  (want: false)")
Write-Output ("  max_phantom_processes: " + (Get-Verdict "max_phantom_processes") + "  (want: 2147483647)")
Write-Output ""
Write-Output "next: root go/no-go per docs/PLAN-deck.md phase 2, then deck/bootstrap.md"
