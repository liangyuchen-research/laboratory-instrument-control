# Minimal TCP example in Windows PowerShell: one JSON line out, one JSON line back.
# Practice against the simulator first (run Start-Demo.cmd; it listens on 127.0.0.1:8767).
# For the real RPi use its IP (for example 192.168.0.82) and the token from the RPi.
param([string]$RpiHost = '127.0.0.1', [int]$Port = 8767, [string]$Token = '')

$client = New-Object System.Net.Sockets.TcpClient($RpiHost, $Port)
$stream = $client.GetStream()
$utf8 = New-Object System.Text.UTF8Encoding($false)
$writer = New-Object System.IO.StreamWriter($stream, $utf8)
$writer.NewLine = "`n"
$writer.AutoFlush = $true
$reader = New-Object System.IO.StreamReader($stream, $utf8)

function Ask([string]$line) {
    $writer.WriteLine($line)
    return $reader.ReadLine() | ConvertFrom-Json
}

$hello = Ask '{"cmd":"hello"}'
"server $($hello.version)  sim=$($hello.sim)  auth_required=$($hello.auth_required)"
if ($hello.auth_required) { Ask ('{"cmd":"auth","token":"' + $Token + '"}') | Out-Null }

$status = Ask '{"cmd":"status","fresh":true}'
"HSPY run/stop = $($status.snapshot.power.run_stop_setting)   set = $($status.snapshot.power.set_voltage_v) V"
"Nano state    = $($status.snapshot.nano.state)   fault = $($status.snapshot.nano.fault)"
"ready for plasma = $($status.ready.plasma)"

$client.Close()
