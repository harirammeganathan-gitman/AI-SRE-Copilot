@echo off
setlocal

for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"

echo Restarting AI SRE Copilot application services on ports 8000, 8001, and 8502...
echo PostgreSQL data and the Ollama service will be left running.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
"$root = (Resolve-Path '%PROJECT_ROOT%').Path.TrimEnd('\'); ^
$ports = @(8000, 8001, 8502); ^
$allowedNames = @('python.exe', 'pythonw.exe', 'uvicorn.exe', 'chroma.exe', 'streamlit.exe'); ^
$targets = New-Object 'System.Collections.Generic.List[int]'; ^
$processes = @(Get-CimInstance Win32_Process); ^
foreach ($port in $ports) { ^
  $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue); ^
  foreach ($connection in $connections) { ^
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$connection.OwningProcess); ^
    while ($process -and ($allowedNames -contains $process.Name.ToLowerInvariant())) { ^
      $executablePath = [string]$process.ExecutablePath; ^
      $commandLine = [string]$process.CommandLine; ^
      $isProjectProcess = $executablePath.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or $commandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0; ^
      if (-not $isProjectProcess) { break }; ^
      $processId = [int]$process.ProcessId; ^
      if (-not $targets.Contains($processId)) { $targets.Add($processId); Write-Output ('Stopping PID {0} ({1}) for port {2}.' -f $processId, $process.Name, $port) }; ^
      $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$process.ParentProcessId); ^
    } ^
  } ^
}; ^
foreach ($process in $processes) { ^
  $executablePath = [string]$process.ExecutablePath; ^
  $commandLine = [string]$process.CommandLine; ^
  $isProjectExecutable = $executablePath.StartsWith($root + '\.venv\Scripts\', [StringComparison]::OrdinalIgnoreCase); ^
  if (($allowedNames -contains $process.Name.ToLowerInvariant()) -and $isProjectExecutable -and ($commandLine -match '(?i)(uvicorn|streamlit|chroma)')) { ^
    $processId = [int]$process.ProcessId; ^
    if (-not $targets.Contains($processId)) { $targets.Add($processId); Write-Output ('Stopping project service PID {0} ({1}).' -f $processId, $process.Name) }; ^
  } ^
}; ^
if ($targets.Count -eq 0) { Write-Output 'No project-owned service processes were found on ports 8000, 8001, or 8502.' }; ^
for ($index = $targets.Count - 1; $index -ge 0; $index--) { Stop-Process -Id $targets[$index] -Force -ErrorAction SilentlyContinue }"
if errorlevel 1 (
    echo Could not stop the project processes. Check PowerShell permissions and try again.
    pause
    exit /b 1
)

timeout /t 2 /nobreak >nul
call :assert_stopped "http://127.0.0.1:8000/health/live" "FastAPI"
if errorlevel 1 goto :restart_failed
call :assert_stopped "http://127.0.0.1:8001/api/v2/heartbeat" "ChromaDB"
if errorlevel 1 goto :restart_failed
call :assert_stopped "http://127.0.0.1:8502/_stcore/health" "Streamlit"
if errorlevel 1 goto :restart_failed
call "%PROJECT_ROOT%\start-ai-sre-copilot.bat"
exit /b %errorlevel%

:assert_stopped
curl.exe -fsS --max-time 2 "%~1" -o nul >nul 2>&1
if not errorlevel 1 (
    echo %~2 is still responding. Close its existing console window and run restart again.
    exit /b 1
)
exit /b 0

:restart_failed
echo Restart aborted because an old service is still responding.
pause
exit /b 1
