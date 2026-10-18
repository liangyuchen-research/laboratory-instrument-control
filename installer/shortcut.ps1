# Creates one shortcut (.lnk). Used by install.py. ASCII only on purpose.
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$WorkDir = '',
    [string]$Description = '',
    [string]$Dir = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if (-not $Dir) { $Dir = [Environment]::GetFolderPath('Desktop') }
$path = Join-Path $Dir ($Name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $Target
if ($WorkDir) { $link.WorkingDirectory = $WorkDir }
if ($Description) { $link.Description = $Description }
$link.Save()
Write-Output $path
