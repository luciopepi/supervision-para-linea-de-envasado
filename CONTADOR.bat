@echo off
chcp 65001 >nul
title Contador de Botellas - NO CERRAR esta ventana
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo.
  echo [ERROR] La aplicacion todavia no esta instalada en esta computadora.
  echo.
  echo Ejecuta primero INSTALAR.bat ^(doble clic^) y despues volve a intentar.
  echo.
  pause
  exit /b 1
)

echo ==========================================================
echo    CONTADOR DE BOTELLAS
echo ==========================================================
echo.
echo Dejá esta ventana abierta mientras uses la aplicacion.
echo El tablero se abre solo en el navegador.
echo Para cerrar todo: Ctrl+C aca, o cerra esta ventana.
echo.

call "venv\Scripts\activate.bat"
python -m contador_botellas --abrir-navegador %*

echo.
echo ==========================================================
echo    La aplicacion se cerro. Ya podes cerrar esta ventana.
echo ==========================================================
pause
