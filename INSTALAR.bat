@echo off
chcp 65001 >nul
title Instalacion - Contador de Botellas
cd /d "%~dp0"

echo ==========================================================
echo    CONTADOR DE BOTELLAS - Instalacion
echo ==========================================================
echo.
echo Esto prepara la aplicacion en esta computadora y deja un
echo acceso directo en el Escritorio. Tarda unos minutos la
echo primera vez (descarga las librerias de vision).
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No se encontro Python en esta computadora.
  echo.
  echo Instalalo desde https://www.python.org/downloads/
  echo IMPORTANTE: al instalarlo, tildar "Add Python to PATH".
  echo Despues volve a ejecutar este INSTALAR.bat
  echo.
  pause
  exit /b 1
)

if exist "venv\Scripts\python.exe" (
  echo [1/3] El entorno ya estaba creado.
) else (
  echo [1/3] Creando el entorno de la aplicacion...
  python -m venv venv
  if errorlevel 1 (
    echo [ERROR] No se pudo crear el entorno.
    pause
    exit /b 1
  )
)

echo [2/3] Instalando las librerias necesarias...
call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Fallo la instalacion de librerias.
  echo Revisa que haya internet y volve a ejecutar INSTALAR.bat
  pause
  exit /b 1
)

echo [3/3] Creando el acceso directo en el Escritorio...
set "CARPETA=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$c=$env:CARPETA; $a=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Contador de Botellas.lnk'); $a.TargetPath=$c+'CONTADOR.bat'; $a.WorkingDirectory=$c; $a.IconLocation=$c+'contador.ico'; $a.Description='Contador e inspector de botellas de la linea de envasado'; $a.Save()"
if errorlevel 1 (
  echo [AVISO] No se pudo crear el acceso directo automaticamente.
  echo Podes usar igual la aplicacion con doble clic en CONTADOR.bat
)

echo.
echo ==========================================================
echo    LISTO
echo ==========================================================
echo.
echo En el Escritorio quedo el icono "Contador de Botellas".
echo Doble clic ahi para usar la aplicacion: se abre sola en el
echo navegador. Todo lo demas (camara, modelo, valvula) se
echo configura desde el engranaje que esta arriba a la derecha
echo de la pantalla.
echo.
pause
