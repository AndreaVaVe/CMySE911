@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ===========================================================================
REM  CMySE 911 · Carga de la fuente nueva en DW2026
REM  Ejecuta los 5 pasos en orden y se detiene en el primero que falle.
REM
REM  Uso:  doble clic, o desde una consola:   correr.cmd
REM  Opcional:  correr.cmd NOMBRE_SERVIDOR
REM ===========================================================================

set "SQLCMD=C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\180\Tools\Binn\SQLCMD.EXE"
set "DIR=%~dp0"
set "SRV=%~1"
if "%SRV%"=="" set "SRV={SERVIDOR}"

echo.
echo ============================================================
echo   Carga de 1.100.708 incidentes en DW2026
echo   Servidor: %SRV%
echo ============================================================
echo.

if not exist "%SQLCMD%" (
    echo [ERROR] No se encuentra sqlcmd en:
    echo         %SQLCMD%
    echo         Ajusta la variable SQLCMD de este archivo.
    goto :fin_error
)

REM --- 0. Comprobar conexion ------------------------------------------------
echo [0/5] Comprobando conexion...
"%SQLCMD%" -S %SRV% -E -C -b -h -1 -W -Q "SET NOCOUNT ON; SELECT 'conectado a ' + DB_NAME() + ' / ' + CAST(SERVERPROPERTY('ProductVersion') AS varchar(20));" 
if errorlevel 1 (
    echo.
    echo [ERROR] No se pudo conectar a %SRV% con autenticacion de Windows.
    echo         Prueba:  correr.cmd localhost
    echo         O revisa que el servicio MSSQLSERVER este levantado.
    goto :fin_error
)

REM --- 1. Tabla de staging --------------------------------------------------
echo.
echo [1/5] Creando stg.imp_incidente ...
"%SQLCMD%" -S %SRV% -E -C -b -I -f 65001 -i "%DIR%01_ddl_staging.sql"
if errorlevel 1 goto :error

REM --- 2. Carga del CSV -----------------------------------------------------
echo.
echo [2/5] Cargando el CSV en el staging (esto tarda un poco) ...
"%SQLCMD%" -S %SRV% -E -C -b -I -f 65001 -i "%DIR%02_carga_staging.sql"
if errorlevel 1 (
    echo.
    echo  Si el error es "Cannot open file", la cuenta de servicio de SQL Server
    echo  no puede leer:
    echo      %DIR%..\datos\dw2026
    echo  Solucion: ejecuta en PowerShell como administrador
    echo      icacls "%DIR%..\datos\dw2026" /grant "NT Service\MSSQLSERVER:(OI)(CI)R" /T
    goto :error
)

REM --- 3. Altas de zona -----------------------------------------------------
echo.
echo [3/5] Dando de alta los barrios que faltaban en DW.Dimension_Zona ...
"%SQLCMD%" -S %SRV% -E -C -b -I -f 65001 -i "%DIR%03_dim_zona_alta.sql"
if errorlevel 1 goto :error

REM --- 4. Procedimiento -----------------------------------------------------
echo.
echo [4/5] Creando el procedimiento de carga ...
"%SQLCMD%" -S %SRV% -E -C -b -I -f 65001 -i "%DIR%04_usp_cargar_hechos_desde_staging.sql"
if errorlevel 1 goto :error

REM --- 5. Carga y verificacion ---------------------------------------------
echo.
echo [5/5] Cargando los hechos y verificando ...
"%SQLCMD%" -S %SRV% -E -C -b -I -f 65001 -W -s "|" -i "%DIR%05_ejecutar_y_validar.sql"
if errorlevel 1 goto :error

echo.
echo ============================================================
echo   TERMINADO. Revisa arriba que los 8 años digan OK
echo   y que TotalIncidentes sea 1100708.
echo.
echo   Falta reprocesar el cubo CUBO2023 en SSAS.
echo ============================================================
goto :fin

:error
echo.
echo ============================================================
echo   DETENIDO: el paso anterior devolvio error.
echo   No se ejecutaron los pasos siguientes.
echo ============================================================
:fin_error

:fin
echo.
pause
endlocal
