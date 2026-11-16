@echo off
rem ==========================================================================
rem  _entorno_qgis.bat - Entorno unico para ejecutar los scripts del CMySE 911
rem ==========================================================================
rem  Resuelve tres problemas reales de este equipo:
rem
rem  1. QGIS escribe su perfil en %APPDATA%\Roaming, que esta fuera del area
rem     permitida por el sandbox -> PermissionError y crash. Se redirige el
rem     perfil dentro del proyecto con QGIS_CUSTOM_CONFIG_PATH (ademas aisla el
rem     analisis de la configuracion del QGIS de escritorio: reproducibilidad).
rem  2. Matplotlib intenta cachear en %USERPROFILE%\.matplotlib -> se redirige
rem     con MPLCONFIGDIR.
rem  3. El modulo 'processing' no esta en el PYTHONPATH del lanzador; el script
rem     Python lo agrega desde QGIS_PREFIX_PATH.
rem
rem  Uso:  _entorno_qgis.bat <script.py> [argumentos]
rem ==========================================================================
setlocal
set "QGIS_ROOT=C:\Program Files\QGIS 3.44.11"

rem Raiz del proyecto = carpeta padre de este .bat
for %%I in ("%~dp0..") do set "PROYECTO=%%~fI"

set "MPLCONFIGDIR=%PROYECTO%\.cache\mpl"
set "QGIS_CUSTOM_CONFIG_PATH=%PROYECTO%\.qgisprofile"
if not exist "%MPLCONFIGDIR%" mkdir "%MPLCONFIGDIR%"
if not exist "%QGIS_CUSTOM_CONFIG_PATH%" mkdir "%QGIS_CUSTOM_CONFIG_PATH%"

rem UTF-8 obligatorio: PowerShell 5.1 y cmd escriben UTF-16/ANSI por defecto
rem y QGIS/PDAL/Python leen UTF-8. Sin esto aparecen acentos corruptos.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "GDAL_FILENAME_IS_UTF8=YES"

rem Sin pantalla: los scripts corren sin interfaz grafica
set "QT_QPA_PLATFORM=offscreen"

rem CRITICO: el plugin 'offscreen' de Qt no usa el motor de fuentes de Windows.
rem Sin QT_QPA_FONTDIR, Qt no encuentra ninguna fuente y TODO EL TEXTO de los
rem mapas, leyendas y PDF se renderiza como rectangulos negros.
if not defined QT_QPA_FONTDIR set "QT_QPA_FONTDIR=%WINDIR%\Fonts"

call "%QGIS_ROOT%\bin\python-qgis-ltr.bat" %*
exit /b %ERRORLEVEL%
