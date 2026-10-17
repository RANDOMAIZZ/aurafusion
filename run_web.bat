@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [AuraFusion web] проверка Python...
where pythonw >nul 2>nul
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: pythonw не найден в PATH. Переустановите Python с галочкой "Add to PATH".
  pause
  exit /b 1
)
echo [AuraFusion web] проверка зависимостей...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: не ставятся зависимости.
  pause
  exit /b 1
)
echo [AuraFusion web] проверка OpenRGB...
tasklist /FI "IMAGENAME eq OpenRGB.exe" 2>nul | find /i "OpenRGB.exe" >nul
if errorlevel 1 (
  if exist "OpenRGB\OpenRGB.exe" (
    echo [AuraFusion web] запускаю локальный OpenRGB...
    start "" /belownormal "OpenRGB\OpenRGB.exe" --server --server-host 127.0.0.1 --server-port 6742 --startminimized
  ) else if exist "E:\MusicRGB\OpenRGB\OpenRGB.exe" (
    echo [AuraFusion web] запускаю OpenRGB из MusicRGB...
    start "" /belownormal "E:\MusicRGB\OpenRGB\OpenRGB.exe" --server --server-host 127.0.0.1 --server-port 6742 --startminimized
  ) else (
    echo [AuraFusion web] OpenRGB.exe не найден, интерфейс будет ждать сервер...
  )
) else (
  echo [AuraFusion web] OpenRGB уже запущен.
)
rem десктопную панель глушим: она держит тот же звук и ту же подсветку
taskkill /FI "IMAGENAME eq pythonw.exe" >nul 2>nul
ping -n 2 127.0.0.1 >nul
echo [AuraFusion web] запуск, открою http://127.0.0.1:8765/
start "AuraFusion web" /belownormal pythonw web_main.py
ping -n 8 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if errorlevel 1 (
  echo [AuraFusion web] ОШИБКА: не запустилось. Для диагностики: python web_main.py
  pause
  exit /b 1
)
echo [AuraFusion web] работает на http://127.0.0.1:8765/  Это окно закроется само.
ping -n 4 127.0.0.1 >nul
exit /b 0