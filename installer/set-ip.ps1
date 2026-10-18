# Sets a fixed IPv4 address on one network adapter. Windows asks for administrator approval.
# Used by install.py only after the person chose the adapter and confirmed.
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$Mask = '255.255.255.0'
)
$ErrorActionPreference = 'Stop'
$arguments = @('interface', 'ipv4', 'set', 'address', ('name="{0}"' -f $Name), 'static', $Ip, $Mask)
$process = Start-Process -FilePath netsh.exe -ArgumentList $arguments -Verb RunAs -Wait -PassThru
exit $process.ExitCode
