param(
    [switch]$Demo,
    [switch]$Foreground,
    [switch]$Stop,
    [switch]$Restart,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$MonitorArguments = @()
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskSource = Join-Path $PSScriptRoot 'src\python\monitor.py'
$taskOutput = Join-Path $PSScriptRoot 'output'

function Find-TaskPython {
    $candidates = @()
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch 'WindowsApps') {
            $candidates += @{ Path = $command.Source; Prefix = @() }
        }
    }
    $candidates += @{ Path = (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'); Prefix = @() }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $candidates += @{ Path = $launcher.Source; Prefix = @('-3') } }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate.Path) {
            try {
                $probe = @($candidate.Prefix) + @('-c', 'import json,sys; sys.exit(2) if sys.version_info < (3,10) else None; print(json.dumps(sys.executable))')
                $result = & $candidate.Path @probe 2>$null
                if ($LASTEXITCODE -eq 0) { return ($result | ConvertFrom-Json) }
            } catch { }
        }
    }
    throw 'Python 3.10+ is required. Install Python, then run start.cmd again.'
}

function Invoke-TaskApi($Instance, [string]$Path, [string]$Method = 'GET') {
    $request = [System.Net.HttpWebRequest]::Create($Instance.origin + $Path)
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 1500
    $request.ReadWriteTimeout = 1500
    $request.KeepAlive = $false
    $request.Method = $Method
    $request.Headers.Add('X-Monitor-Token', $Instance.token)
    if ($Method -eq 'POST') {
        $body = [System.Text.Encoding]::UTF8.GetBytes('{}')
        $request.ContentType = 'application/json'
        $request.ContentLength = $body.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($body, 0, $body.Length) } finally { $stream.Dispose() }
    }
    $response = $request.GetResponse()
    try {
        if ([int]$response.StatusCode -ne 200) { throw 'Monitor request failed.' }
    } finally { $response.Dispose() }
}

function Read-TaskInstance {
    if (-not (Test-Path -LiteralPath $taskState)) { return $null }
    try {
        $instance = Get-Content -LiteralPath $taskState -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$instance.pid -le 0 -or $instance.root -ne $PSScriptRoot -or
            $instance.origin -notmatch '^http://127\.0\.0\.1:[0-9]+$' -or -not $instance.token) { return $null }
        $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$instance.pid)
        if (-not $process -or $process.CommandLine -notmatch [regex]::Escape($taskSource)) { return $null }
        Invoke-TaskApi $instance '/api/state'
        return $instance
    } catch { return $null }
}

function Stop-TaskInstance($Instance) {
    $process = Get-Process -Id ([int]$Instance.pid) -ErrorAction Stop
    Invoke-TaskApi $Instance '/api/shutdown' 'POST'
    if (-not $process.WaitForExit(15000)) {
        throw 'Monitor shutdown timed out. Check the background logs before retrying.'
    }
}

$taskLock = $null
try {
    if ($Stop -and $Restart) { throw 'Choose either Stop or Restart.' }
    $taskPort = 8765
    for ($index = 0; $index -lt $MonitorArguments.Count; $index++) {
        if ($MonitorArguments[$index] -eq '--port') {
            $index++
            if ($index -ge $MonitorArguments.Count) { throw '--port requires a value.' }
            $taskPort = [int]$MonitorArguments[$index]
        } elseif ($MonitorArguments[$index] -match '^--port=(.*)$') {
            $taskPort = [int]$Matches[1]
        }
    }
    if ($taskPort -lt 0 -or $taskPort -gt 65535) { throw 'Port must be between 0 and 65535.' }
    $taskState = Join-Path $taskOutput ('background-' + $taskPort + '.json')
    New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
    $taskLockDeadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        try {
            $taskLock = [System.IO.File]::Open($taskState + '.lock', [System.IO.FileMode]::OpenOrCreate,
                                              [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        } catch [System.IO.IOException] { Start-Sleep -Milliseconds 250 }
    } while (-not $taskLock -and [DateTime]::UtcNow -lt $taskLockDeadline)
    if (-not $taskLock) { throw 'Another launcher is still running. Retry shortly.' }
    $taskInstance = Read-TaskInstance
    if ($Stop) {
        if ($taskInstance) {
            Stop-TaskInstance $taskInstance
            Write-Host 'Monitor stopped. SSH connections closed.'
        } else { Write-Host 'No managed monitor is running on this port.' }
        exit 0
    }
    if ($Restart -and $taskInstance) {
        Stop-TaskInstance $taskInstance
        $MonitorArguments = @($taskInstance.launch_arguments) + @($MonitorArguments)
        $taskInstance = $null
    }
    $taskBaseArgs = @()
    if ($Demo) { $taskBaseArgs += '--demo' }
    $taskBaseArgs += @($MonitorArguments)
    $taskNoBrowser = $taskBaseArgs -contains '--no-browser'
    $taskConsole = $Foreground -or $taskBaseArgs -contains '--once' -or
                   $taskBaseArgs -contains '--help' -or $taskBaseArgs -contains '-h'
    if ($taskInstance -and -not $taskConsole) {
        Write-Host ('Monitor is already running: ' + $taskInstance.origin)
        if (-not $taskNoBrowser) { Start-Process $taskInstance.origin }
        exit 0
    }
    $taskPython = Find-TaskPython
    if ($taskConsole) {
        & $taskPython $taskSource @taskBaseArgs
        exit $LASTEXITCODE
    }
    $taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
    $taskStdout = Join-Path $taskOutput ('background-' + $taskPort + '-' + $taskStamp + '.stdout.log')
    $taskStderr = Join-Path $taskOutput ('background-' + $taskPort + '-' + $taskStamp + '.stderr.log')
    $taskLaunchArgs = @($taskStdout, $taskStderr) + $taskBaseArgs + @('--no-browser', '--instance-file', $taskState)
    $taskChildId = & $taskPython (Join-Path $PSScriptRoot 'src\python\background.py') @taskLaunchArgs
    if ($LASTEXITCODE -ne 0) { throw ('Monitor failed to start. See ' + $taskStderr) }
    $taskChild = Get-Process -Id ([int]$taskChildId) -ErrorAction SilentlyContinue
    if (-not $taskChild) { throw ('Monitor failed to start. See ' + $taskStderr) }
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $taskInstance = Read-TaskInstance
        if ($taskInstance -and [int]$taskInstance.pid -eq $taskChild.Id) { break }
        if ($taskChild.HasExited) { throw ('Monitor failed to start. See ' + $taskStderr) }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    if (-not $taskInstance -or [int]$taskInstance.pid -ne $taskChild.Id) {
        throw ('Monitor startup timed out. See ' + $taskStderr)
    }
    $taskInstance | Add-Member -NotePropertyName launch_arguments -NotePropertyValue @($taskBaseArgs) -Force
    $taskInstance | Add-Member -NotePropertyName stdout_log -NotePropertyValue $taskStdout -Force
    $taskInstance | Add-Member -NotePropertyName stderr_log -NotePropertyValue $taskStderr -Force
    $taskTemporary = $taskState + '.launcher.tmp'
    [System.IO.File]::WriteAllText($taskTemporary, ($taskInstance | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding $false))
    Move-Item -LiteralPath $taskTemporary -Destination $taskState -Force
    Write-Host ('Background monitor started: ' + $taskInstance.origin)
    Write-Host ('Logs: ' + $taskStdout + ' / ' + $taskStderr)
    if (-not $taskNoBrowser) { Start-Process $taskInstance.origin }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 2
} finally {
    if ($taskLock) { $taskLock.Dispose() }
}
