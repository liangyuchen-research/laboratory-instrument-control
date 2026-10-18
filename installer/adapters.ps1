# Lists the wired network adapters of this PC as JSON. Read-only. Used by install.py.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$rows = @(Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq '802.3' } | Sort-Object Name | ForEach-Object {
    $index = $_.ifIndex
    [pscustomobject]@{
        name        = $_.Name
        description = $_.InterfaceDescription
        connected   = ($_.Status -eq 'Up')
        addresses   = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
        gateway     = [bool](Get-NetRoute -InterfaceIndex $index -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
    }
})
ConvertTo-Json -InputObject $rows -Compress -Depth 4
