param([switch]$Demo, [Parameter(ValueFromRemainingArguments = $true)][string[]]$MonitorArguments)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPythonCandidates = @()
foreach ($taskPythonName in @('python', 'python3')) {
    $taskPythonCommand = Get-Command $taskPythonName -ErrorAction SilentlyContinue
    if ($taskPythonCommand -and $taskPythonCommand.Source -notmatch 'WindowsApps') {
        $taskPythonCandidates += $taskPythonCommand.Source
    }
}
$taskPythonCandidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
foreach ($taskPythonPath in $taskPythonCandidates) {
    if (Test-Path -LiteralPath $taskPythonPath) {
        & $taskPythonPath -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $taskMonitorArgs = @((Join-Path $PSScriptRoot 'src\python\monitor.py'))
            if ($Demo) { $taskMonitorArgs += '--demo' }
            $taskMonitorArgs += $MonitorArguments
            & $taskPythonPath @taskMonitorArgs
            exit $LASTEXITCODE
        }
    }
}
$taskPythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($taskPythonLauncher) {
    & $taskPythonLauncher.Source -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $taskMonitorArgs = @('-3', (Join-Path $PSScriptRoot 'src\python\monitor.py'))
        if ($Demo) { $taskMonitorArgs += '--demo' }
        $taskMonitorArgs += $MonitorArguments
        & $taskPythonLauncher.Source @taskMonitorArgs
        exit $LASTEXITCODE
    }
}
Write-Host 'Python 3.10+ is required. Install Python, then run start.cmd again.' -ForegroundColor Red
exit 2
