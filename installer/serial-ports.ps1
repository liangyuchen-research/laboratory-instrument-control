# Lists USB serial devices of this PC as JSON, including ones whose driver is missing.
# Read-only. Used by flash_nano.py. ASCII only on purpose.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$rows = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_[0-9A-Fa-f]{4}' } | Where-Object { $_.Name -match '\(COM\d+\)' -or $_.ConfigManagerErrorCode -ne 0 } | ForEach-Object {
    $port = ''
    if ($_.Name -match '\((COM\d+)\)') { $port = $Matches[1] }
    $vendor = ''
    if ($_.PNPDeviceID -match 'VID_([0-9A-Fa-f]{4})') { $vendor = $Matches[1].ToUpper() }
    $product = ''
    if ($_.PNPDeviceID -match 'PID_([0-9A-Fa-f]{4})') { $product = $Matches[1].ToUpper() }
    [pscustomobject]@{
        name   = [string]$_.Name
        port   = $port
        vid    = $vendor
        pid    = $product
        error  = [int]$_.ConfigManagerErrorCode
    }
})
ConvertTo-Json -InputObject $rows -Compress -Depth 3
