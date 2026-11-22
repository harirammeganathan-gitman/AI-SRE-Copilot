@echo off
setlocal

for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%" || goto :failed

set "POSTGRES_CONTAINER=ai-sre-copilot-postgres"
set "POSTGRES_RUNNING="

if not exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    echo Python virtual environment not found at .venv.
    echo Create it and install the project dependencies first.
    goto :failed
)

where.exe docker >nul 2>&1
if errorlevel 1 (
    echo Docker CLI was not found. Install Docker Desktop and try again.
    goto :failed
)

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop is not running. Start Docker Desktop and try again.
    goto :failed
)

for /f "delims=" %%S in ('docker inspect -f "{{.State.Running}}" "%POSTGRES_CONTAINER%" 2^>nul') do set "POSTGRES_RUNNING=%%S"
if /I not "%POSTGRES_RUNNING%"=="true" (
    echo Starting PostgreSQL container...
    docker start "%POSTGRES_CONTAINER%" >nul
    if errorlevel 1 (
        echo Could not start container %POSTGRES_CONTAINER%.
        echo Confirm that the container exists and Docker Desktop is running.
        goto :failed
    )
)

call :wait_for_postgres
if errorlevel 1 (
    echo PostgreSQL did not become ready. Check the container in Docker Desktop.
    goto :failed
)

call :http_ok "http://127.0.0.1:11434/api/tags"
if errorlevel 1 (
    where.exe ollama >nul 2>&1
    if errorlevel 1 (
        echo Ollama is unavailable and the ollama command was not found.
        goto :failed
    )
    echo Starting Ollama...
    start "AI SRE Ollama" cmd /k "ollama serve"
    call :wait_for_http "http://127.0.0.1:11434/api/tags" "Ollama"
    if errorlevel 1 goto :failed
)

call :http_ok "http://127.0.0.1:8001/api/v2/heartbeat"
if errorlevel 1 (
    if not exist "%PROJECT_ROOT%\.venv\Scripts\chroma.exe" (
        echo ChromaDB executable not found in .venv.
        goto :failed
    )
    echo Starting ChromaDB...
    start "AI SRE ChromaDB" /D "%PROJECT_ROOT%" "%PROJECT_ROOT%\.venv\Scripts\chroma.exe" run --host 127.0.0.1 --port 8001 --path "%PROJECT_ROOT%\chroma_data"
    call :wait_for_http "http://127.0.0.1:8001/api/v2/heartbeat" "ChromaDB"
    if errorlevel 1 goto :failed
)

call :http_ok "http://127.0.0.1:8000/health/live"
if errorlevel 1 (
    echo Starting FastAPI...
    start "AI SRE FastAPI" /D "%PROJECT_ROOT%" "%PROJECT_ROOT%\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
    call :wait_for_http "http://127.0.0.1:8000/health/live" "FastAPI"
    if errorlevel 1 goto :failed
) else (
    echo FastAPI is already running. Use restart-ai-sre-copilot.bat to reload code changes.
)

call :http_ok "http://127.0.0.1:8502/_stcore/health"
if errorlevel 1 (
    "%PROJECT_ROOT%\.venv\Scripts\python.exe" -m streamlit --version >nul 2>&1
    if errorlevel 1 (
        echo Streamlit module not found in the project virtual environment.
        goto :failed
    )
    echo Starting Streamlit...
    start "AI SRE Streamlit" /D "%PROJECT_ROOT%" "%PROJECT_ROOT%\.venv\Scripts\python.exe" -m streamlit run frontend\app.py --server.port 8502
    call :wait_for_http "http://127.0.0.1:8502/_stcore/health" "Streamlit"
    if errorlevel 1 goto :failed
) else (
    echo Streamlit is already running. Use restart-ai-sre-copilot.bat to reload code changes.
)

echo.
echo AI SRE Copilot services are running.
echo API:      http://localhost:8000
echo Readiness: http://localhost:8000/health/ready
echo Streamlit: http://localhost:8502
call :http_ok "http://127.0.0.1:8000/health/ready"
if errorlevel 1 echo Warning: FastAPI is running but reports not-ready. Check its terminal and .env.
echo.
pause
exit /b 0

:wait_for_postgres
for /L %%I in (1,1,30) do (
    docker exec "%POSTGRES_CONTAINER%" pg_isready -q -U ai_sre_copilot -d ai_sre_copilot >nul 2>&1
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
exit /b 1

:http_ok
curl.exe -fsS --max-time 2 "%~1" -o nul >nul 2>&1
exit /b %errorlevel%

:wait_for_http
set "CHECK_URL=%~1"
set "SERVICE_NAME=%~2"
for /L %%I in (1,1,30) do (
    curl.exe -fsS --max-time 2 "%CHECK_URL%" -o nul >nul 2>&1
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
echo %SERVICE_NAME% did not respond at %CHECK_URL%.
exit /b 1

:failed
echo.
echo Startup stopped. Resolve the message above, then run this file again.
pause
exit /b 1
