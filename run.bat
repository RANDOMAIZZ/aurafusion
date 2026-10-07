@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [AuraFusion] проверка Python...
where pythonw >nul 2>nul
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: pythonw не найден в PATH. Переустановите Python с галочкой "Add to PATH".
  pause
  exit /b 1
)
echo [AuraFusion] проверка зависимостей...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: не ставятся зависимости. Проверьте интернет и запустите вручную: python -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo [AuraFusion] проверка OpenRGB...
tasklist /FI "IMAGENAME eq OpenRGB.exe" 2>nul | find /i "OpenRGB.exe" >nul
if errorlevel 1 (
  if exist "OpenRGB\OpenRGB.exe" (
    echo [AuraFusion] запускаю локальный OpenRGB...
    start "" /belownormal "OpenRGB\OpenRGB.exe" --server --server-host 127.0.0.1 --server-port 6742 --startminimized
  ) else if exist "E:\MusicRGB\OpenRGB\OpenRGB.exe" (
    echo [AuraFusion] запускаю OpenRGB из MusicRGB...
    start "" /belownormal "E:\MusicRGB\OpenRGB\OpenRGB.exe" --server --server-host 127.0.0.1 --server-port 6742 --startminimized
  ) else (
    echo [AuraFusion] OpenRGB.exe не найден, интерфейс будет ждать сервер...
  )
) else (
  echo [AuraFusion] OpenRGB уже запущен.
)
echo [AuraFusion] запуск...
start "AuraFusion" /belownormal pythonw main.py
rem ждём ~6с (ping вместо timeout: timeout падает без консоли), потом одна проверка
ping -n 7 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if errorlevel 1 (
  echo [AuraFusion] ОШИБКА: программа не запустилась. Для диагностики запустите вручную: python main.py
  pause
  exit /b 1
)
echo [AuraFusion] работает. Это окно закроется само.
ping -n 4 127.0.0.1 >nul
exit /b 0
