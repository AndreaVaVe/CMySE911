<#
.SYNOPSIS
    Orquestador del pipeline geoespacial del CMySE 911.

.DESCRIPTION
    Ejecuta en secuencia los tres scripts del pipeline, controlando tiempos,
    codigos de salida y verificando que cada entregable se haya generado.

      1. 01_normalizar_911.py   KML/GeoPackage -> GeoPackage maestro (EPSG:5345)
      2. 02_analisis_espacial.py KDE, DBSCAN, K-Means, zonas calientes, recursos
      3. 03_cartografia.py      Proyecto QGIS, estilos .qml, Atlas PDF

    Pensado para re-ejecutarse cada vez que ingresan nuevos lotes del 911:
    editar 04_scripts\config_911.json y volver a lanzar este script.

.PARAMETER SoloNormalizar
    Ejecuta unicamente el paso 1.

.PARAMETER SoloAnalisis
    Ejecuta unicamente el paso 2.

.PARAMETER SoloCartografia
    Ejecuta unicamente el paso 3.

.PARAMETER Verificar
    No ejecuta nada: solo comprueba que existan las fuentes del config y los
    entregables esperados, e informa el estado.

.EXAMPLE
    .\ejecutar_pipeline.ps1
    .\ejecutar_pipeline.ps1 -Verificar
    .\ejecutar_pipeline.ps1 -SoloAnalisis

.NOTES
    No usa rutas absolutas de Python ni de QGIS: delega todo el entorno en
    _entorno_qgis.bat, que es el unico punto donde vive la configuracion.
#>
[CmdletBinding()]
param(
    [switch]$SoloNormalizar,
    [switch]$SoloAnalisis,
    [switch]$SoloCartografia,
    [switch]$Verificar
)

$ErrorActionPreference = 'Stop'

$Raiz       = Split-Path -Parent $PSScriptRoot
$Entorno    = Join-Path $PSScriptRoot '_entorno_qgis.bat'
$Config     = Join-Path $PSScriptRoot 'config_911.json'
$DirInformes = Join-Path $Raiz '05_informes'
$Marca      = Get-Date -Format 'yyyyMMdd_HHmmss'
$Log        = Join-Path $DirInformes "pipeline_$Marca.log"

if (-not (Test-Path $DirInformes)) { New-Item -ItemType Directory -Path $DirInformes -Force | Out-Null }

function Escribir {
    param([string]$Texto, [string]$Color = 'Gray')
    Write-Host $Texto -ForegroundColor $Color
    Add-Content -Path $Log -Value $Texto -Encoding UTF8
}

function Leer-Config {
    if (-not (Test-Path $Config)) { return $null }
    try {
        return (Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        Escribir "  ADVERTENCIA: config_911.json ilegible ($($_.Exception.Message)). Se usan los valores por defecto de cada script." 'Yellow'
        return $null
    }
}

function Probar-Fuentes {
    param($Cfg)
    if ($null -eq $Cfg) { return $true }
    $ok = $true
    $listas = @(
        @{ Nombre = 'incidentes';        Campo = 'incidentes' },
        @{ Nombre = 'jurisdicciones';    Campo = 'jurisdicciones' },
        @{ Nombre = 'puntos_operativos'; Campo = 'puntos_operativos' }
    )
    foreach ($lista in $listas) {
        $items = $Cfg.($lista.Campo)
        if ($null -eq $items) { continue }
        foreach ($item in $items) {
            $ruta = $item[0]
            if (-not (Test-Path $ruta)) {
                Escribir ("  FALTA fuente de {0}: {1}" -f $lista.Nombre, $ruta) 'Red'
                $ok = $false
            }
        }
    }
    foreach ($kml in $Cfg.zonas_cardinales) {
        if (-not (Test-Path $kml)) {
            Escribir "  FALTA zona cardinal: $kml" 'Red'
            $ok = $false
        }
    }
    return $ok
}

# --------------------------------------------------------------------------
# Definicion de pasos
# --------------------------------------------------------------------------
$pasos = @(
    @{
        Nombre   = '1/6 Normalizacion'
        Script   = '01_normalizar_911.py'
        Entregables = @(
            '01_gpkg\CMySE911_maestro.gpkg',
            '05_informes\QA_normalizacion.md'
        )
    },
    @{
        Nombre   = '2/6 Analisis espacial'
        Script   = '02_analisis_espacial.py'
        Entregables = @(
            '02_analisis\CMySE911_analisis.gpkg',
            '02_analisis\kde_incidentes.tif',
            '05_informes\ANALISIS_operativo.md'
        )
    },
    @{
        Nombre   = '3/6 Cartografia'
        Script   = '03_cartografia.py'
        Entregables = @(
            '03_cartografia\CMySE911.qgz',
            '03_cartografia\vista_previa.png',
            '03_cartografia\atlas_zonas_calientes.pdf'
        )
    },
    @{
        Nombre   = '4/6 Verificacion del proyecto'
        Script   = '04_verificar_proyecto.py'
        Entregables = @()
    },
    @{
        Nombre   = '5/6 Congelar linea base'
        Script   = '05_congelar_baseline.py'
        Entregables = @(
            '05_informes\BASELINE_2022.md',
            '05_informes\baseline_2022.json'
        )
    },
    @{
        Nombre   = '6/6 Analisis de sensibilidad'
        Script   = '06_sensibilidad.py'
        Entregables = @(
            '05_informes\SENSIBILIDAD.md'
        )
    }
)

if ($SoloNormalizar)   { $pasos = $pasos[0..0] }
if ($SoloAnalisis)     { $pasos = $pasos[1..1] }
if ($SoloCartografia)  { $pasos = $pasos[2..2] }

# --------------------------------------------------------------------------
# Encabezado
# --------------------------------------------------------------------------
Escribir ('=' * 72)
Escribir 'PIPELINE GEOESPACIAL CMySE 911'
Escribir ('=' * 72)
Escribir "Raiz del proyecto : $Raiz"
Escribir "Registro          : $Log"
Escribir "Fecha             : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

if (-not (Test-Path $Entorno)) {
    Escribir "ERROR: no se encuentra $Entorno" 'Red'
    exit 2
}

$cfg = Leer-Config
if ($null -ne $cfg) {
    Escribir "Configuracion     : $Config"
    Escribir ("CRS destino       : EPSG:{0}" -f $cfg.crs_destino)
    Escribir ("Incidentes        : {0} fuente(s)" -f @($cfg.incidentes).Count)
} else {
    Escribir 'Configuracion     : valores por defecto de cada script' 'Yellow'
}

Escribir ''
Escribir 'Comprobacion de fuentes:'
$fuentesOk = Probar-Fuentes -Cfg $cfg
if ($fuentesOk) { Escribir '  Todas las fuentes declaradas existen.' 'Green' }

if ($Verificar) {
    Escribir ''
    Escribir 'Estado de entregables:'
    foreach ($paso in $pasos) {
        foreach ($rel in $paso.Entregables) {
            $ruta = Join-Path $Raiz $rel
            if (Test-Path $ruta) {
                $mb = [math]::Round((Get-Item $ruta).Length / 1MB, 2)
                Escribir ("  OK      {0}  ({1} MB)" -f $rel, $mb) 'Green'
            } else {
                Escribir ("  FALTA   {0}" -f $rel) 'Red'
            }
        }
    }
    exit 0
}

if (-not $fuentesOk) {
    Escribir ''
    Escribir 'ERROR: hay fuentes faltantes. Corregir config_911.json antes de continuar.' 'Red'
    exit 3
}

# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------
$resultados = @()

foreach ($paso in $pasos) {
    $script = Join-Path $PSScriptRoot $paso.Script
    Escribir ''
    Escribir ('-' * 72)
    Escribir ("{0}  ->  {1}" -f $paso.Nombre, $paso.Script) 'Cyan'
    Escribir ('-' * 72)

    if (-not (Test-Path $script)) {
        Escribir "  ERROR: no existe $script" 'Red'
        $resultados += [pscustomobject]@{ Paso = $paso.Nombre; Salida = 'NO EJECUTADO'; Segundos = 0; Entregables = 'script ausente' }
        continue
    }

    $inicio = Get-Date
    # La salida de QGIS es muy verbosa (avisos de Qt): va al registro, no a pantalla.
    cmd /c "`"$Entorno`" `"$script`" >> `"$Log`" 2>&1"
    $codigo = $LASTEXITCODE
    $segundos = [math]::Round(((Get-Date) - $inicio).TotalSeconds, 1)

    if ($codigo -eq 0) {
        Escribir ("  Finalizado OK en {0} s" -f $segundos) 'Green'
    } else {
        Escribir ("  FALLO con codigo {0} tras {1} s. Ver el registro." -f $codigo, $segundos) 'Red'
        Escribir ("  Ultimas lineas del registro:")
        Get-Content $Log -Tail 12 -Encoding UTF8 | ForEach-Object { Escribir "    $_" 'DarkGray' }
    }

    $estadoEntregables = @()
    foreach ($rel in $paso.Entregables) {
        $ruta = Join-Path $Raiz $rel
        if (Test-Path $ruta) {
            $estadoEntregables += ('OK ' + (Split-Path $rel -Leaf))
        } else {
            $estadoEntregables += ('FALTA ' + (Split-Path $rel -Leaf))
        }
    }

    $resultados += [pscustomobject]@{
        Paso        = $paso.Nombre
        Salida      = if ($codigo -eq 0) { 'OK' } else { "codigo $codigo" }
        Segundos    = $segundos
        Entregables = ($estadoEntregables -join ' | ')
    }

    if ($codigo -ne 0) { break }
}

# --------------------------------------------------------------------------
# Resumen
# --------------------------------------------------------------------------
Escribir ''
Escribir ('=' * 72)
Escribir 'RESUMEN'
Escribir ('=' * 72)
$resultados | Format-Table -AutoSize | Out-String -Width 200 | ForEach-Object { Escribir $_ }

$fallos = @($resultados | Where-Object { $_.Salida -ne 'OK' }).Count
if ($fallos -eq 0) {
    Escribir 'Pipeline completado sin errores.' 'Green'
    Escribir ''
    Escribir 'Entregables principales:'
    Escribir ("  Proyecto QGIS : {0}" -f (Join-Path $Raiz '03_cartografia\CMySE911.qgz'))
    Escribir ("  Vista previa  : {0}" -f (Join-Path $Raiz '03_cartografia\vista_previa.png'))
    Escribir ("  Atlas PDF     : {0}" -f (Join-Path $Raiz '03_cartografia\atlas_zonas_calientes.pdf'))
    Escribir ("  GeoPackage    : {0}" -f (Join-Path $Raiz '01_gpkg\CMySE911_maestro.gpkg'))
    Escribir ("  Analisis      : {0}" -f (Join-Path $Raiz '02_analisis\CMySE911_analisis.gpkg'))
    exit 0
} else {
    Escribir ("Pipeline con {0} paso(s) fallido(s). Revisar {1}" -f $fallos, $Log) 'Red'
    exit 1
}
