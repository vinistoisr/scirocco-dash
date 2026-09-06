# readserial.ps1 -- restart code.py on the Feather and capture its serial output.
#
#   .\tools\readserial.ps1                 # auto-detect port, restart, listen 40s
#   .\tools\readserial.ps1 -Seconds 120    # longer, e.g. to watch a DID scan start
#   .\tools\readserial.ps1 -NoReload       # just listen, do not restart the program
#   .\tools\readserial.ps1 -Port COM7      # override auto-detection
#
# This is the only way to see what the board is saying. The e-ink shows coarse
# status only. Note the port disappears while the board reboots, so if you get
# "could not open", wait two seconds and run it again.

param(
    [int]$Seconds = 40,
    [string]$Port = "",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

if (-not $Port) {
    $candidates = @(Get-CimInstance Win32_PnPEntity |
        Where-Object { $_.Name -match "COM\d+" -and $_.Name -match "USB Serial|CircuitPython" } |
        ForEach-Object { if ($_.Name -match "(COM\d+)") { $Matches[1] } })

    if ($candidates.Count -eq 0) {
        Write-Output "No CircuitPython serial port found. Is the board plugged in?"
        Write-Output "All COM ports currently present:"
        Get-CimInstance Win32_PnPEntity |
            Where-Object { $_.Name -match "COM\d+" } |
            ForEach-Object { Write-Output ("  " + $_.Name) }
        exit 1
    }
    if ($candidates.Count -gt 1) {
        Write-Output ("Multiple candidates: " + ($candidates -join ", ") + ". Re-run with -Port.")
        exit 1
    }
    $Port = $candidates[0]
}

Write-Output "listening on $Port for ${Seconds}s"

$sp = New-Object System.IO.Ports.SerialPort($Port, 115200, "None", 8, "One")
$sp.ReadTimeout = 500
$sp.DtrEnable = $true
try {
    $sp.Open()
} catch {
    Write-Output "could not open ${Port}: $_"
    Write-Output "the port vanishes briefly while the board reboots; try again in a moment"
    exit 1
}

Start-Sleep -Milliseconds 400
$sp.DiscardInBuffer()

if (-not $NoReload) {
    # Ctrl-C breaks the running program into the REPL, Ctrl-D reloads code.py
    $sp.Write([char]3)
    Start-Sleep -Milliseconds 700
    $sp.Write([char]4)
}

$deadline = (Get-Date).AddSeconds($Seconds)
$sb = New-Object System.Text.StringBuilder
while ((Get-Date) -lt $deadline) {
    try {
        $chunk = $sp.ReadExisting()
        if ($chunk) { [void]$sb.Append($chunk) }
    } catch { }
    Start-Sleep -Milliseconds 200
}
$sp.Close()

# strip the ANSI escapes CircuitPython 10 emits for its title bar
$text = $sb.ToString() -replace "\x1b\][^\x07\x1b]*(\x07|\x1b\)", "" -replace "\x1b\[[0-9;]*[A-Za-z]", ""
Write-Output $text
