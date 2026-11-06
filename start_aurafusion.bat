@echo off
rem ============================================================
rem  AuraFusion — универсальный запуск
rem  Работает из любой папки и с любого диска: пути не зашиты.
rem
rem    start_aurafusion.bat           обычный запуск (откроет панель в браузере)
rem    start_aurafusion.bat --no-browser   без открытия браузера
rem    start_aurafusion.bat fast      не проверять зависимости (быстрее)
rem    start_aurafusion.bat stop      остановить AuraFusion
rem
rem  Что делает:
rem    1. находит Python (pythonw) и ставит зависимости;
rem    2. находит Skydimo и запускает её в ТИХОМ режиме (без окна, значок в
rem       трее) — расширения в нём работают, в skydimo-simple.exe их нет;
rem    3. гасит прошлый экземпляр AuraFusion (только его, по командной строке);
rem    4. запускает web_main.py и ждёт, пока панель ответит.
rem ============================================================
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PORT=8765"
set "FAST="
set "STOP="
set "PASS="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="fast" (set "FAST=1") else if /i "%~1"=="stop" (set "STOP=1") else (set "PASS=%PASS% %~1")
shift
goto args
:args_done

rem ---------- остановка прошлого экземпляра ----------
rem По командной строке, а не по имени pythonw.exe: чужие скрипты не трогаем.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'web_main\.py' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
if defined STOP (
  echo [AuraFusion] остановлен.
  exit /b 0
)

rem ---------- Python ----------
set "PYW="
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
  rem запасной путь: pythonw лежит рядом с python.exe
  for /f "delims=" %%Q in ('where python 2^>nul') do (
    if not defined PYW if exist "%%~dpQpythonw.exe" set "PYW=%%~dpQpythonw.exe"
  )
)
if not defined PYW (
  echo [AuraFusion] ОШИБКА: Python не найден. Установите Python 3.10+ с галочкой "Add to PATH".
  pause
  exit /b 1
)
set "PYC=%PYW:pythonw.exe=python.exe%"

if not defined FAST (
  echo [AuraFusion] проверка зависимостей...
  "%PYC%" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [AuraFusion] ОШИБКА: не ставятся зависимости.
    pause
    exit /b 1
  )
)

rem ---------- Skydimo: тихий режим, без окна, но с расширениями ----------
rem Launcher.exe --autostart-silent поднимает только skydimo-core (значок в
rem трее, окна нет), и расширение AuraFusion в нём работает. Простой режим
rem (skydimo-simple.exe / core --simple) расширения не грузит — его гасим.
set "SKY="
for %%D in ("%ProgramFiles%\Skydimo" "%ProgramFiles(x86)%\Skydimo" "%LOCALAPPDATA%\Programs\Skydimo" "%LOCALAPPDATA%\Skydimo") do (
  if not defined SKY if exist "%%~D\Launcher.exe" set "SKY=%%~D"
)
if defined SKY (
  powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'skydimo-simple.exe' -or ($_.Name -eq 'skydimo-core.exe' -and $_.CommandLine -match '--simple') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
  tasklist /FI "IMAGENAME eq skydimo-core.exe" 2>nul | find /i "skydimo-core.exe" >nul
  if errorlevel 1 (
    echo [AuraFusion] запускаю Skydimo в тихом режиме...
    start "" "%SKY%\Launcher.exe" --autostart-silent
    ping -n 7 127.0.0.1 >nul
  ) else (
    echo [AuraFusion] Skydimo уже запущена.
  )
) else (
  echo [AuraFusion] Skydimo не найдена — пропускаю ^(лента Skydimo работать не будет^).
)

rem ---------- запуск ----------
ping -n 2 127.0.0.1 >nul
echo [AuraFusion] запуск...
start "AuraFusion" /belownormal "%PYW%" web_main.py --port %PORT%%PASS%

rem ---------- ждём ответа панели (до 30 с) ----------
powershell -NoProfile -Command "$ok=$false; 1..30 | ForEach-Object { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%PORT%/api/state | Out-Null; $ok=$true; break } catch { Start-Sleep 1 } }; if ($ok) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: панель не ответила. Для диагностики: "%PYC%" web_main.py
  pause
  exit /b 1
)
echo [AuraFusion] работает: http://127.0.0.1:%PORT%/
echo [AuraFusion] Окно закроется само. Остановить: значок в трее или "start_aurafusion.bat stop".
ping -n 4 127.0.0.1 >nul
exit /b 0
