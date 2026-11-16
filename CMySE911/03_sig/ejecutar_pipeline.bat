@echo off
rem ==========================================================================
rem  ejecutar_pipeline.bat - Lanzador del pipeline CMySE 911
rem ==========================================================================
rem  Windows suele traer deshabilitada la ejecucion de scripts .ps1 (politica
rem  "Restricted"), y entonces aparece este error al usar ejecutar_pipeline.ps1:
rem      "la ejecucion de scripts esta deshabilitada en este sistema"
rem
rem  Este lanzador invoca PowerShell habilitando la ejecucion SOLO para esta
rem  corrida. No cambia ninguna configuracion del sistema ni del usuario, y se
rem  puede ejecutar con doble clic desde el Explorador.
rem
rem  Uso:
rem      ejecutar_pipeline.bat                 pipeline completo
rem      ejecutar_pipeline.bat -Verificar      solo controlar fuentes y salidas
rem      ejecutar_pipeline.bat -SoloAnalisis   un solo paso
rem      ejecutar_pipeline.bat -SoloCartografia
rem ==========================================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ejecutar_pipeline.ps1" %*
exit /b %ERRORLEVEL%
