# readframes.ps1 -- capture raw bytes from the Feather and decode RealDash
# CAN '44' frames, so the wire format can be checked before trusting it on
# the head unit.
#
#   .\tools\readframes.ps1 -Port COM5 -Seconds 8

param(
    [string]$Port = "COM5",
    [int]$Seconds = 8,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$sp = New-Object System.IO.Ports.SerialPort($Port, 115200, "None", 8, "One")
$sp.ReadTimeout = 500
$sp.DtrEnable = $true
$sp.Open()
Start-Sleep -Milliseconds 300
$sp.DiscardInBuffer()

if ($Reload) {
    # Ctrl-C to break into the REPL, Ctrl-D to restart code.py. Note the
    # startup e-ink refresh blocks for ~15-25 s before the first frame, so
    # capture well past that.
    $sp.Write([char]3)
    Start-Sleep -Milliseconds 700
    $sp.Write([char]4)
    Start-Sleep -Milliseconds 500
    $sp.DiscardInBuffer()
}

$buf = New-Object System.Collections.Generic.List[byte]
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    $n = $sp.BytesToRead
    if ($n -gt 0) {
        $tmp = New-Object byte[] $n
        [void]$sp.Read($tmp, 0, $n)
        $buf.AddRange($tmp)
    }
    Start-Sleep -Milliseconds 50
}
$sp.Close()

$bytes = $buf.ToArray()
Write-Output "captured $($bytes.Length) bytes"

$lines = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -le $bytes.Length - 16; $i++) {
    if ($bytes[$i] -eq 0x44 -and $bytes[$i+1] -eq 0x33 -and
        $bytes[$i+2] -eq 0x22 -and $bytes[$i+3] -eq 0x11) {

        $id = [BitConverter]::ToUInt32($bytes, $i + 4)
        $w = @()
        for ($k = 0; $k -lt 4; $k++) {
            $w += [BitConverter]::ToUInt16($bytes, $i + 8 + ($k * 2))
        }
        $hex = ($bytes[($i)..($i+15)] | ForEach-Object { "{0:X2}" -f $_ }) -join " "
        [void]$lines.Add(("id 0x{0:X3}  words {1,6} {2,6} {3,6} {4,6}   [{5}]" -f `
            $id, $w[0], $w[1], $w[2], $w[3], $hex))
        $i += 15
    }
}
if ($lines.Count -eq 0) {
    Write-Output "no RealDash frames found in capture"
} else {
    # Show the tail, not the head: the first cycles include channel setup
    # and a reconnect, so early status frames understate the sample rate.
    Write-Output "$($lines.Count) frames decoded; last 12:"
    $lines | Select-Object -Last 12
}
