/* ============================================================================
   DW2026 · PASO 5 de 5 · Ejecutar la carga y verificar
   ----------------------------------------------------------------------------
   OJO: reemplaza los hechos del rango 2018-2025. La serie antigua de 1.148.409
   llamadas deja de estar en DW.Hechos_Atencion.

   El bloque de respaldo es OPCIONAL pero recomendado la primera vez. Si no lo
   quieres, comenta las dos lineas marcadas.
   ============================================================================ */

USE DW2026;
GO
SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   0. Respaldo de los hechos actuales (opcional)
   --------------------------------------------------------------------------- */
IF OBJECT_ID('DW.Hechos_Atencion_previo') IS NULL
BEGIN
    SELECT * INTO DW.Hechos_Atencion_previo FROM DW.Hechos_Atencion;   -- opcional
    DECLARE @filasBak bigint = (SELECT COUNT_BIG(*) FROM DW.Hechos_Atencion_previo);
    PRINT CONCAT(N'Respaldo creado: DW.Hechos_Atencion_previo con ', @filasBak, N' filas.');
END
ELSE
    PRINT 'Ya existe DW.Hechos_Atencion_previo; no se sobrescribe.';

/* ---------------------------------------------------------------------------
   1. Carga
   --------------------------------------------------------------------------- */
EXEC dbo.Usp_Cargar_Hechos_DesdeStaging
     @FechaDesde = '2018-01-01',
     @FechaHasta = '2025-12-31',
     @Validar    = 1;
GO

/* ---------------------------------------------------------------------------
   2. Verificacion contra la tabla de control del contrato
   --------------------------------------------------------------------------- */
;WITH control AS (
    SELECT * FROM (VALUES
        (2018, 178765), (2019, 162066), (2020, 178792), (2021, 136842),
        (2022, 117134), (2023, 121389), (2024, 107723), (2025,  97997)
    ) v(Anio, Esperado)
),
real AS (
    SELECT t.ANIO AS Anio, SUM(h.CANTIDAD_LLAMADAS) AS Obtenido
    FROM DW.Hechos_Atencion h
    INNER JOIN DW.Dimension_Tiempo t ON t.FECHAKEY = h.FECHAKEY
    GROUP BY t.ANIO
)
SELECT  c.Anio,
        c.Esperado,
        CONVERT(bigint, ISNULL(r.Obtenido, 0))                     AS Obtenido,
        CONVERT(bigint, ISNULL(r.Obtenido, 0)) - c.Esperado        AS Diferencia,
        CASE WHEN ISNULL(r.Obtenido, 0) = c.Esperado THEN 'OK' ELSE 'FALLA' END AS Estado
FROM control c
LEFT JOIN real r ON r.Anio = c.Anio
ORDER BY c.Anio;
GO

SELECT  COUNT_BIG(*)                     AS FilasHecho,
        CONVERT(bigint, SUM(CANTIDAD_LLAMADAS)) AS TotalIncidentes,
        MIN(FECHAKEY)                    AS FechaMin,
        MAX(FECHAKEY)                    AS FechaMax
FROM DW.Hechos_Atencion;
GO

/* Cobertura territorial: cuantos incidentes caen en la fila centinela */
SELECT  z.ZONA,
        CONVERT(bigint, SUM(h.CANTIDAD_LLAMADAS)) AS Incidentes
FROM DW.Hechos_Atencion h
INNER JOIN DW.Dimension_Zona z ON z.COD_ZONAKEY = h.COD_ZONAKEY
GROUP BY z.ZONA
ORDER BY Incidentes DESC;
GO

/* Duplicados de grano: debe devolver 0 filas */
SELECT  COD_ZONAKEY, COD_CODIGOKEY, COD_CLASIFICADORKEY, FECHAKEY, COUNT(*) AS Veces
FROM DW.Hechos_Atencion
GROUP BY COD_ZONAKEY, COD_CODIGOKEY, COD_CLASIFICADORKEY, FECHAKEY
HAVING COUNT(*) > 1;
GO
