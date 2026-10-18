# Sends one request to the Nano over its USB serial port (9600 8N1) and prints the reply as hex.
# Used by flash_nano.py after flashing to read back the firmware version. ASCII only on purpose.
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [Parameter(Mandatory = $true)][string]$Hex,
    [int]$Expect = 85,
    [int]$SettleMs = 2500
)
$ErrorActionPreference = 'Stop'
$request = New-Object byte[] ($Hex.Length / 2)
for ($i = 0; $i -lt $request.Length; $i++) { $request[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16) }
$serial = New-Object System.IO.Ports.SerialPort $Port, 9600, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$serial.ReadTimeout = 300
$serial.WriteTimeout = 1000
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.Open()
try {
    # Opening the port may reset the Nano; give the bootloader time to hand over to the firmware.
    Start-Sleep -Milliseconds $SettleMs
    $buffer = New-Object byte[] 256
    $got = 0
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $serial.DiscardInBuffer()
        $got = 0
        $serial.Write($request, 0, $request.Length)
        $deadline = [DateTime]::Now.AddMilliseconds(1200)
        while ($got -lt $Expect -and [DateTime]::Now -lt $deadline) {
            try { $got += $serial.Read($buffer, $got, $Expect - $got) } catch [TimeoutException] { }
        }
        if ($got -ge $Expect) { break }
        Start-Sleep -Milliseconds 800
    }
    Write-Output (([BitConverter]::ToString($buffer, 0, $got)) -replace '-', '')
} finally {
    $serial.Close()
}
