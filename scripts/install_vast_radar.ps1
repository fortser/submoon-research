# Автозапуск текущего пользователя при входе Windows. Без admin/аренды/ключей.
param([switch]$Remove, [switch]$StartNow)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$script = Join-Path $root 'scripts\vast_radar.py'
if (!(Test-Path -LiteralPath $python)) { throw 'Project pythonw.exe not found' }
$arguments = '-X utf8 "' + $script + '" --desktop'
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'Submoon Vast Radar.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $linkPath) {
    $existing = $shell.CreateShortcut($linkPath)
    if ($existing.TargetPath -ne $python -or $existing.Arguments -ne $arguments) {
        throw 'Startup shortcut belongs to another installation; not overwriting'
    }
}
if ($Remove) {
    if (Test-Path -LiteralPath $linkPath) { Remove-Item -LiteralPath $linkPath }
    Write-Output 'Autostart removed. Running monitor is not stopped.'
    exit 0
}
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $python
$link.Arguments = $arguments
$link.WorkingDirectory = $root
$link.Description = 'Submoon: read-only Vast.ai radar, 15 minute polling'
$link.Save()
# Проверяем фактический ярлык, а не только успешный выход COM.
$check = $shell.CreateShortcut($linkPath)
if ($check.TargetPath -ne $python -or $check.Arguments -ne $arguments) { throw 'Shortcut verification failed' }
Write-Output ('Autostart installed: ' + $linkPath)
if ($StartNow) {
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -PassThru
    Write-Output ('Launch PID: ' + $process.Id)
}
