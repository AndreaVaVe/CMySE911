# ============================================================================
#  CMYSE911 · Verificar el cubo por anio
# ----------------------------------------------------------------------------
#  Descubre medidas y jerarquias con $system.MDSCHEMA_* y localiza las columnas
#  del result set POR NOMBRE, no por posicion: el proveedor MDAC devuelve el
#  eje de filas (el anio) antes que la medida, al contrario de lo intuitivo.
#
#  Uso:
#    powershell -ExecutionPolicy Bypass -File "verificar_cubo_mdx.ps1"
# ============================================================================
$ErrorActionPreference = 'Continue'

$servidor = '{SERVIDOR}\SSAS2026'
$esperado = 1100708
$control  = @{ 2018=178765; 2019=162066; 2020=178792; 2021=136842;
               2022=117134; 2023=121389; 2024=107723; 2025=97997 }

$cn = $null
foreach ($cat in @('CUBO2023','CUBO2026')) {
    try {
        $cs = "Provider=MSOLAP;Data Source=$servidor;Initial Catalog=$cat;Integrated Security=SSPI;"
        $cn = New-Object System.Data.OleDb.OleDbConnection $cs
        $cn.Open(); $catalogo = $cat; break
    } catch { }
}
if (-not $cn) { Write-Host "ERROR: no conecta a $servidor"; exit 1 }
Write-Host "Conectado: $servidor / $catalogo"
$cmd = $cn.CreateCommand()

function Campos($rd) {
    $c = @()
    for ($i = 0; $i -lt $rd.FieldCount; $i++) { $c += [string]$rd.GetName($i) }
    return $c
}

# --- 1. Medida --------------------------------------------------------------
$cmd.CommandText = 'SELECT * FROM $system.MDSCHEMA_MEASURES'
$rd = $cmd.ExecuteReader()
$medidaU = $null; $cuboU = $null
while ($rd.Read()) {
    $nombre = [string]$rd['MEASURE_NAME']
    if ($nombre -match 'CANTIDAD' -and -not $medidaU) {
        $medidaU = [string]$rd['MEASURE_UNIQUE_NAME']
        $cuboNm  = [string]$rd['CUBE_NAME']
        $cuboU   = '[' + $cuboNm + ']'
    }
}
$rd.Close()
if (-not $medidaU) { Write-Host 'ERROR: no encuentro la medida de cantidad.'; $cn.Close(); exit 1 }
Write-Host "Medida: $medidaU    Cubo: $cuboU"

# --- 2. Jerarquia de anio ---------------------------------------------------
$cmd.CommandText = 'SELECT * FROM $system.MDSCHEMA_HIERARCHIES'
$rd = $cmd.ExecuteReader()
$jerarquia = $null
while ($rd.Read()) {
    $h = [string]$rd['HIERARCHY_UNIQUE_NAME']
    if ($h -match 'Tiempo' -and $h -match 'nio') { if (-not $jerarquia) { $jerarquia = $h } }
}
$rd.Close()
if (-not $jerarquia) { Write-Host 'ERROR: no encuentro jerarquia de anio.'; $cn.Close(); exit 1 }
Write-Host "Jerarquia de anio: $jerarquia"

# --- 3. Total (una sola celda) ---------------------------------------------
Write-Host ''
Write-Host '=== Total del cubo ==='
$cmd.CommandText = "SELECT {$medidaU} ON COLUMNS FROM $cuboU"
$rd = $cmd.ExecuteReader()
$campos = Campos $rd
Write-Host ('  columnas: ' + ($campos -join ' | '))
while ($rd.Read()) {
    $valores = @()
    for ($i = 0; $i -lt $rd.FieldCount; $i++) { $valores += $rd.GetValue($i) }
    Write-Host ('  valores : ' + (($valores | ForEach-Object { [string]$_ }) -join ' | '))
}
$rd.Close()

# --- 4. Por anio, localizando columnas por nombre --------------------------
Write-Host ''
Write-Host '=== Por anio ==='
$cmd.CommandText = "SELECT {$medidaU} ON COLUMNS, NON EMPTY $jerarquia.CHILDREN ON ROWS FROM $cuboU"
$rd = $cmd.ExecuteReader()
$campos = Campos $rd
Write-Host ('  columnas: ' + ($campos -join ' | '))

# indice de la medida: por nombre; si no aparece, la ultima columna
$iMed = -1
for ($i = 0; $i -lt $campos.Count; $i++) { if ($campos[$i] -match 'CANTIDAD|Measures') { $iMed = $i; break } }
if ($iMed -lt 0) { $iMed = $campos.Count - 1 }
# indice del anio: la primera columna que no sea la medida
$iAnio = -1
for ($i = 0; $i -lt $campos.Count; $i++) { if ($i -ne $iMed) { $iAnio = $i; break } }
Write-Host ("  medida en columna {0} [{1}] · anio en columna {2} [{3}]" -f $iMed, $campos[$iMed], $iAnio, $(if ($iAnio -ge 0) { $campos[$iAnio] } else { '-' }))
Write-Host ''

$suma = 0; $n = 0
Write-Host ('  {0,-14} {1,12} {2,12}  {3}' -f 'Anio', 'Cubo', 'Control', 'Estado')
while ($rd.Read()) {
    $v    = [int64]$rd.GetValue($iMed)
    $et   = if ($iAnio -ge 0) { [string]$rd.GetValue($iAnio) } else { '' }
    $anio = 0
    if ($et -match '(\d{4})') { $anio = [int]$matches[1] }
    $ctrl = $null
    if ($anio -and $control.ContainsKey($anio)) { $ctrl = $control[$anio] }
    $st = '?'
    if ($null -ne $ctrl) { if ($v -eq $ctrl) { $st = 'OK' } else { $st = 'FALLA' } }
    Write-Host ('  {0,-14} {1,12:N0} {2,12}  {3}' -f $et, $v, $(if ($null -ne $ctrl) { '{0:N0}' -f $ctrl } else { '-' }), $st)
    $suma += $v; $n++
}
$rd.Close()

Write-Host ''
Write-Host ("  Filas: $n   Suma: {0:N0}" -f $suma)
if ($suma -eq $esperado) { Write-Host "  OK: el cubo cuadra con {0:N0}" -f $esperado }
else { Write-Host ("  NO cuadra: diferencia {0:N0}" -f ($suma - $esperado)) }

$cn.Close()
Write-Host ''
Write-Host 'Terminado.'
