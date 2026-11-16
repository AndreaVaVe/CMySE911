/* ============================================================================
   DW2026 · PASO 2 de 5 · Cargar el staging desde el CSV
   ----------------------------------------------------------------------------
   Origen: datos\dw2026\imp_incidente.csv  (1.100.708 filas, UTF-8 con BOM)

   La cuenta de servicio de SQL Server debe poder leer @raiz. Si no puede:
       icacls "<carpeta>" /grant "NT Service\MSSQLSERVER:(OI)(CI)R" /T
   o copiar el CSV a C:\Bulk911 y ajustar @raiz.

   Idempotente: trunca y recarga.
   ============================================================================ */

USE DW2026;
GO
SET NOCOUNT ON;
GO

DECLARE @raiz nvarchar(400) =
    N'C:\Users\{USUARIO}\Documents\deepseek-harness\default-workspace\CMYSE911_v1\datos\dw2026\';
DECLARE @sql nvarchar(max);
DECLARE @t0 datetime2(0) = SYSDATETIME();

TRUNCATE TABLE stg.imp_incidente;

SET @sql = N'
BULK INSERT stg.imp_incidente
FROM ''' + @raiz + N'imp_incidente.csv''
WITH (
    FORMAT      = ''CSV'',
    FIRSTROW    = 2,
    CODEPAGE    = ''65001'',
    FIELDQUOTE  = ''"'',
    ROWTERMINATOR = ''0x0d0a'',
    TABLOCK
);';
EXEC sp_executesql @sql;

DECLARE @filas bigint = (SELECT COUNT_BIG(*) FROM stg.imp_incidente);
DECLARE @sinBarrio bigint = (SELECT COUNT_BIG(*) FROM stg.imp_incidente WHERE Barrio IS NULL OR Barrio = '');
DECLARE @desde date = (SELECT MIN(CAST(FechaHora AS date)) FROM stg.imp_incidente);
DECLARE @hasta date = (SELECT MAX(CAST(FechaHora AS date)) FROM stg.imp_incidente);

PRINT CONCAT(N'Filas cargadas: ', @filas,
             N' | sin barrio: ', @sinBarrio,
             N' | rango: ', CONVERT(varchar(10), @desde, 120), N' a ', CONVERT(varchar(10), @hasta, 120),
             N' | ', DATEDIFF(SECOND, @t0, SYSDATETIME()), N' s');

IF @filas <> 1100708
BEGIN
    THROW 50100,
        N'El staging no tiene 1.100.708 filas. Revisar que datos\dw2026\imp_incidente.csv sea de la ultima corrida de paso5_staging_dw2026.py.',
        1;
END
GO
