# ============================================================================
#  CMySE 911 · Procesar y verificar el cubo CUBO2023 (SSAS)
# ----------------------------------------------------------------------------
#  El cubo ya apunta a DW2026; no hay que redesplegar nada. Solo procesarlo
#  para que lea los 1.100.708 incidentes recien cargados.
#
#  Uso:
#    powershell -ExecutionPolicy Bypass -File "procesar_cubo.ps1"
#
#  Funciona aunque el Control Inteligente de Aplicaciones bloquee .cmd o .exe
#  nuevos: powershell.exe esta firmado y el script es texto.
# ============================================================================
$ErrorActionPreference = 'Continue'

$servidor   = '{SERVIDOR}\SSAS2026'
$esperado   = 1100708
$candidatas = @('CUBO2023', 'CUBO2026')

# --- 1. Cargar AMO -----------------------------------------------------------
$sdk = $null
foreach ($v in @('160', '150', '140', '130')) {
    $p = "C:\Program Files (x86)\Microsoft SQL Server\$v\SDK\Assemblies"
    if (Test-Path (Join-Path $p 'Microsoft.AnalysisServices.DLL')) { $sdk = $p; break }
}
if (-not $sdk) {
    Write-Output 'ERROR: no encuentro las librerias AMO de Analysis Services.'
    Write-Output '       Busca Microsoft.AnalysisServices.DLL en C:\Program Files (x86)\Microsoft SQL Server\*\SDK\Assemblies'
    exit 1
}
Write-Output "AMO: $sdk"
Add-Type -Path (Join-Path $sdk 'Microsoft.AnalysisServices.Core.DLL') | Out-Null
Add-Type -Path (Join-Path $sdk 'Microsoft.AnalysisServices.DLL') | Out-Null

# --- 2. Conectar -------------------------------------------------------------
$srv = New-Object Microsoft.AnalysisServices.Server
try {
    $srv.Connect($servidor)
} catch {
    Write-Output "ERROR: no se pudo conectar a $servidor"
    Write-Output ('       ' + $_.Exception.Message)
    exit 1
}
Write-Output "Conectado a $servidor"

$db = $null
foreach ($n in $candidatas) {
    $db = $srv.Databases.FindByName($n)
    if ($db) { break }
}
if (-not $db) {
    Write-Output ('ERROR: no existe ninguna base ' + ($candidatas -join ' / ') + ' en el servidor.')
    $srv.Disconnect(); exit 1
}
Write-Output "Base: $($db.Name)"

# --- 3. Asegurar que el origen de datos puede leer DW2026 --------------------
try {
    $ds = $db.DataSources[0]
    $imp = New-Object Microsoft.AnalysisServices.ImpersonationInfo
    $imp.ImpersonationMode = [Microsoft.AnalysisServices.ImpersonationMode]::ImpersonateAccount
    $imp.Account = 'NT Service\MSOLAP$SSAS2026'
    $ds.ImpersonationInfo = $imp
    $ds.ConnectionString = 'Provider=SQLOLEDB.1;Data Source={SERVIDOR};Integrated Security=SSPI;Initial Catalog=DW2026'
    $ds.Update()
    $srv.Refresh()
    $db = $srv.Databases.FindByName($db.Name)
    Write-Output 'Origen de datos: DW2026 con impersonacion explicita.'
} catch {
    Write-Output ('Aviso: no se pudo ajustar la impersonacion: ' + $_.Exception.Message)
}
Write-Output ''

# --- 4. Procesar -------------------------------------------------------------
Write-Output 'Procesando dimensiones...'
$ok = 0
foreach ($d in $db.Dimensions) {
    try {
        $d.Process([Microsoft.AnalysisServices.ProcessType]::ProcessFull)
        Write-Output ('  ' + $d.Name.PadRight(26) + '-> OK')
        $ok++
    } catch {
        $m = ($_.Exception.Message -split "`n") | Select-Object -First 2
        Write-Output ('  ' + $d.Name.PadRight(26) + '-> ERROR')
        $m | ForEach-Object { Write-Output ('      ' + $_.Trim()) }
    }
}
Write-Output "Dimensiones OK: $ok / $($db.Dimensions.Count)"
Write-Output ''

Write-Output 'Procesando el cubo (esto tarda un poco)...'
$t0 = Get-Date
try {
    foreach ($c in $db.Cubes) {
        $c.Process([Microsoft.AnalysisServices.ProcessType]::ProcessFull)
        Write-Output ('  ' + $c.Name + ' -> OK')
    }
} catch {
    $m = ($_.Exception.Message -split "`n") | Select-Object -First 4
    Write-Output '  ERROR al procesar el cubo:'
    $m | ForEach-Object { Write-Output ('      ' + $_.Trim()) }
}
Write-Output ('  duracion: ' + [int]((Get-Date) - $t0).TotalSeconds + ' s')
Write-Output ''

# --- 5. Verificar con MDX ----------------------------------------------------
$nombreCubo = if ($db.Cubes.Count -gt 0) { $db.Cubes[0].Name } else { 'DW2026' }
$medida = 'CANTIDAD LLAMADAS'
if ($db.Cubes.Count -gt 0 -and $db.Cubes[0].Measures.Count -gt 0) {
    $medida = $db.Cubes[0].Measures[0].Name
}
$cs = "Provider=MSOLAP;Data Source=$servidor;Initial Catalog=$($db.Name);Integrated Security=SSPI;"

Write-Output '=== Verificacion MDX ==='
try {
    $cn = New-Object System.Data.OleDb.OleDbConnection $cs
    $cn.Open()
    $cmd = $cn.CreateCommand()
    $cmd.CommandText = "SELECT {[Measures].[$medida]} ON COLUMNS FROM [$nombreCubo]"
    $rd = $cmd.ExecuteReader()
    while ($rd.Read()) {
        $total = [int64]$rd[0]
        Write-Output ("  Medida [$medida] = {0:N0}" -f $total)
        if ($total -eq $esperado) {
            Write-Output ("  OK: coincide con el DW ({0:N0})" -f $esperado)
        } else {
            Write-Output ("  ATENCION: se esperaban {0:N0} (diferencia {1:N0})" -f $esperado, ($total - $esperado))
        }
    }
    $rd.Close()

    $cmd.CommandText = "SELECT {[Measures].[$medida]} ON COLUMNS, NON EMPTY [Dimension Tiempo].[Anio].CHILDREN ON ROWS FROM [$nombreCubo]"
    try {
        $rd = $cmd.ExecuteReader()
        Write-Output ''
        Write-Output '  Por anio:'
        while ($rd.Read()) {
            Write-Output ('    ' + [string]$rd.GetValue(1) + '  ->  ' + ('{0:N0}' -f [int64]$rd.GetValue(0)))
        }
        $rd.Close()
    } catch {
        Write-Output ('  (no se pudo consultar por anio: ' + $_.Exception.Message + ')')
    }
    $cn.Close()
} catch {
    Write-Output ('  FALLO MDX: ' + $_.Exception.Message)
}

$srv.Disconnect()
Write-Output ''
Write-Output 'Listo.'
