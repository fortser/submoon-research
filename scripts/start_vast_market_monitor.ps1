param(
    [ValidateSet(48,72)][int]$Hours = 72,
    [ValidateRange(60,3600)][int]$IntervalSeconds = 300,
    [ValidateRange(0.01,1.0)][double]$MaxPrice = 1.0
)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$scriptPath = Join-Path $projectRoot 'scripts\vast_track_cpu.py'
$sessionName = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$sessionPath = Join-Path $projectRoot ('data\interim\vast_market\' + $sessionName)
$controlPath = Join-Path $projectRoot ('scratch\vast_market_controls\' + $sessionName)
New-Item -ItemType Directory -Path $controlPath -Force | Out-Null
$argsList = @('-X','utf8',('"'+$scriptPath+'"'),'--duration-hours',$Hours,'--interval',$IntervalSeconds,
    '--max-price',$MaxPrice.ToString([Globalization.CultureInfo]::InvariantCulture),'--output',('"'+$sessionPath+'"'))
$process = Start-Process -FilePath $pythonPath -ArgumentList $argsList -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $controlPath 'stdout.log') -RedirectStandardError (Join-Path $controlPath 'stderr.log') -PassThru
$info = @{ pid=$process.Id; session=$sessionPath; hours=$Hours; interval_seconds=$IntervalSeconds;
    max_search_price_usd_hour=$MaxPrice; started_utc=[DateTime]::UtcNow.ToString('o'); mode='read_only_no_rental' }
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $controlPath 'launch.json') -Encoding UTF8
$info | ConvertTo-Json
