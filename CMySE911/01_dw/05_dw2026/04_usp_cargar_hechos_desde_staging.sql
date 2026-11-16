/* ============================================================================
   DW2026 · PASO 4 de 5 · Procedimiento de carga del hecho desde el staging
   ----------------------------------------------------------------------------
   Reemplaza a dbo.Usp_Cargar_Hechos_Atencion, que leia del OLTP
   (CMySE911.dbo.Llamada) y reconciliaba contra el, dos cosas incompatibles con
   la fuente nueva.

   Diferencias con el procedimiento antiguo:
     1. Lee de stg.imp_incidente, no de CMySE911.
     2. Resuelve la zona por NOMBRE de barrio, no por DISTRITO_ID.
     3. Manda los barrios desconocidos y los sin coordenada a la fila centinela
        'SIN GEORREFERENCIA' en vez de descartarlos con el INNER JOIN.
     4. Reconoce contra el staging (1.100.708), no contra el OLTP.
     5. Valida antes de tocar nada: si una fecha, un codigo o un clasificador no
        resuelven, aborta sin escribir.
     6. Resuelve las dimensiones con mapas de UNA fila por clave natural: si una
        dimension tuviera nombres repetidos, el hecho no se multiplica.

   NO modifica el esquema del DW: escribe en DW.Hechos_Atencion tal como esta.

   Reejecutable sin riesgo: CREATE OR ALTER.
   ============================================================================ */

USE DW2026;
GO
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE dbo.Usp_Cargar_Hechos_DesdeStaging
    @FechaDesde date = NULL,
    @FechaHasta date = NULL,
    @Validar    bit  = 1
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @FechaDesde IS NULL SET @FechaDesde = '2018-01-01';
    IF @FechaHasta IS NULL SET @FechaHasta = '2025-12-31';
    IF @FechaDesde > @FechaHasta
        THROW 50001, N'Rango invalido: FechaDesde > FechaHasta.', 1;

    /* ---------------------------------------------------------------
       0. Precondiciones
       --------------------------------------------------------------- */
    IF OBJECT_ID('stg.imp_incidente') IS NULL
        THROW 50010, N'No existe stg.imp_incidente. Ejecutar 01_ddl_staging.sql.', 1;

    IF NOT EXISTS (SELECT 1 FROM stg.imp_incidente)
        THROW 50011, N'stg.imp_incidente esta vacia. Ejecutar 02_carga_staging.sql.', 1;

    DECLARE @zonaND int =
        (SELECT MIN(COD_ZONAKEY) FROM DW.Dimension_Zona
         WHERE ZONA = N'SIN GEORREFERENCIA' AND FECHA_FINAL = '9999-12-31');

    IF @zonaND IS NULL
        THROW 50012, N'Falta la fila SIN GEORREFERENCIA en DW.Dimension_Zona. Ejecutar 03_dim_zona_alta.sql.', 1;

    /* ---------------------------------------------------------------
       1. Mapas de dimension: una fila por clave natural.
          Si hubiera versiones activas repetidas, MIN() deja una sola y evita
          que el JOIN multiplique filas del hecho.
       --------------------------------------------------------------- */
    IF OBJECT_ID('tempdb..#mapZona')   IS NOT NULL DROP TABLE #mapZona;
    IF OBJECT_ID('tempdb..#mapCodigo') IS NOT NULL DROP TABLE #mapCodigo;
    IF OBJECT_ID('tempdb..#mapClasif') IS NOT NULL DROP TABLE #mapClasif;

    SELECT ZONA, COD_ZONAKEY = MIN(COD_ZONAKEY)
    INTO #mapZona
    FROM DW.Dimension_Zona
    WHERE FECHA_FINAL = '9999-12-31'
    GROUP BY ZONA;

    SELECT CODIGO_ID, COD_CODIGOKEY = MIN(COD_CODIGOKEY)
    INTO #mapCodigo
    FROM DW.Dimension_Codigo
    WHERE FECHA_FINAL = '9999-12-31'
    GROUP BY CODIGO_ID;

    SELECT CLASIFICADOR_ID, COD_CLASIFICADORKEY = MIN(COD_CLASIFICADORKEY)
    INTO #mapClasif
    FROM DW.Dimension_Clasificador
    WHERE FECHA_FINAL = '9999-12-31'
    GROUP BY CLASIFICADOR_ID;

    /* Informe de ambiguedades: no bloquea, pero queda constancia. */
    DECLARE @dupZona int = (SELECT COUNT(*) FROM (
        SELECT ZONA FROM DW.Dimension_Zona WHERE FECHA_FINAL = '9999-12-31'
        GROUP BY ZONA HAVING COUNT(*) > 1) x);
    DECLARE @dupCodigo int = (SELECT COUNT(*) FROM (
        SELECT CODIGO_ID FROM DW.Dimension_Codigo WHERE FECHA_FINAL = '9999-12-31'
        GROUP BY CODIGO_ID HAVING COUNT(*) > 1) x);
    DECLARE @dupClasif int = (SELECT COUNT(*) FROM (
        SELECT CLASIFICADOR_ID FROM DW.Dimension_Clasificador WHERE FECHA_FINAL = '9999-12-31'
        GROUP BY CLASIFICADOR_ID HAVING COUNT(*) > 1) x);

    IF @dupZona + @dupCodigo + @dupClasif > 0
        PRINT CONCAT(N'Aviso: claves activas repetidas en dimensiones (zona/codigo/clasif) = ',
                     @dupZona, N'/', @dupCodigo, N'/', @dupClasif,
                     N'. Se tomo la clave menor de cada una.');

    /* ---------------------------------------------------------------
       2. Validaciones que SI bloquean: nada se escribe si algo no resuelve
       --------------------------------------------------------------- */
    DECLARE @msg nvarchar(1000);

    DECLARE @sinTiempo bigint = (
        SELECT COUNT_BIG(*) FROM stg.imp_incidente s
        WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
          AND NOT EXISTS (SELECT 1 FROM DW.Dimension_Tiempo t
                          WHERE t.FECHA = CAST(s.FechaHora AS date)));

    DECLARE @sinCodigo bigint = (
        SELECT COUNT_BIG(*) FROM stg.imp_incidente s
        WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
          AND NOT EXISTS (SELECT 1 FROM #mapCodigo d WHERE d.CODIGO_ID = s.CodigoID));

    DECLARE @sinClasif bigint = (
        SELECT COUNT_BIG(*) FROM stg.imp_incidente s
        WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
          AND NOT EXISTS (SELECT 1 FROM #mapClasif d WHERE d.CLASIFICADOR_ID = s.ClasificadorID));

    IF @sinTiempo > 0 OR @sinCodigo > 0 OR @sinClasif > 0
    BEGIN
        /* Se muestran las claves que faltan para que el problema sea accionable. */
        IF @sinTiempo > 0
            SELECT TOP (20) 'Fecha sin Dimension_Tiempo' AS Problema,
                   CONVERT(varchar(10), CAST(FechaHora AS date), 120) AS Clave, COUNT(*) AS Filas
            FROM stg.imp_incidente s
            WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
              AND NOT EXISTS (SELECT 1 FROM DW.Dimension_Tiempo t
                              WHERE t.FECHA = CAST(s.FechaHora AS date))
            GROUP BY CONVERT(varchar(10), CAST(s.FechaHora AS date), 120) ORDER BY Filas DESC;

        IF @sinCodigo > 0
            SELECT TOP (20) 'Codigo sin Dimension_Codigo' AS Problema,
                   CAST(CodigoID AS varchar(10)) AS Clave, COUNT(*) AS Filas
            FROM stg.imp_incidente s
            WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
              AND NOT EXISTS (SELECT 1 FROM #mapCodigo d WHERE d.CODIGO_ID = s.CodigoID)
            GROUP BY CodigoID ORDER BY Filas DESC;

        IF @sinClasif > 0
            SELECT TOP (20) 'Clasificador sin Dimension_Clasificador' AS Problema,
                   CAST(ClasificadorID AS varchar(10)) AS Clave, COUNT(*) AS Filas
            FROM stg.imp_incidente s
            WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
              AND NOT EXISTS (SELECT 1 FROM #mapClasif d WHERE d.CLASIFICADOR_ID = s.ClasificadorID)
            GROUP BY ClasificadorID ORDER BY Filas DESC;

        SET @msg = CONCAT(N'Validacion previa FALLIDA. Sin fecha: ', @sinTiempo,
                          N'; sin codigo: ', @sinCodigo, N'; sin clasificador: ', @sinClasif,
                          N'. No se escribio nada en DW.Hechos_Atencion.');
        THROW 50013, @msg, 1;
    END

    /* ---------------------------------------------------------------
       3. Agregacion al grano del DW: (zona, codigo, clasificador, fecha)
       --------------------------------------------------------------- */
    DECLARE @filasBorradas bigint = 0, @filasInsertadas bigint = 0;

    BEGIN TRANSACTION;

    ;WITH agregado AS (
        SELECT
            COALESCE(dz.COD_ZONAKEY, @zonaND)  AS COD_ZONAKEY,
            dc.COD_CODIGOKEY                   AS COD_CODIGOKEY,
            dcl.COD_CLASIFICADORKEY            AS COD_CLASIFICADORKEY,
            dt.FECHAKEY                        AS FECHAKEY,
            COUNT(*)                           AS CANTIDAD_LLAMADAS,
            AVG(CAST(s.Latitud  AS float))     AS LATITUD,
            AVG(CAST(s.Longitud AS float))     AS LONGITUD
        FROM stg.imp_incidente s
        INNER JOIN DW.Dimension_Tiempo dt ON dt.FECHA = CAST(s.FechaHora AS date)
        INNER JOIN #mapCodigo   dc        ON dc.CODIGO_ID = s.CodigoID
        INNER JOIN #mapClasif   dcl       ON dcl.CLASIFICADOR_ID = s.ClasificadorID
        LEFT  JOIN #mapZona     dz        ON dz.ZONA = s.Barrio
        WHERE CAST(s.FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta
        GROUP BY COALESCE(dz.COD_ZONAKEY, @zonaND), dc.COD_CODIGOKEY,
                 dcl.COD_CLASIFICADORKEY, dt.FECHAKEY
    )
    SELECT * INTO #O FROM agregado OPTION (MAXDOP 1);

    /* 4. Reemplazo del rango */
    DELETE h
    FROM DW.Hechos_Atencion h
    INNER JOIN DW.Dimension_Tiempo t ON t.FECHAKEY = h.FECHAKEY
    WHERE t.FECHA BETWEEN @FechaDesde AND @FechaHasta
    OPTION (MAXDOP 1);
    SET @filasBorradas = @@ROWCOUNT;

    INSERT INTO DW.Hechos_Atencion
        (COD_ZONAKEY, COD_CODIGOKEY, COD_CLASIFICADORKEY, FECHAKEY,
         CANTIDAD_LLAMADAS, LATITUD, LONGITUD)
    SELECT COD_ZONAKEY, COD_CODIGOKEY, COD_CLASIFICADORKEY, FECHAKEY,
           CANTIDAD_LLAMADAS, LATITUD, LONGITUD
    FROM #O OPTION (MAXDOP 1);
    SET @filasInsertadas = @@ROWCOUNT;

    DROP TABLE #O;

    COMMIT TRANSACTION;

    /* ---------------------------------------------------------------
       5. Reconciliacion contra el staging
       --------------------------------------------------------------- */
    DECLARE @stg bigint = (
        SELECT COUNT_BIG(*) FROM stg.imp_incidente
        WHERE CAST(FechaHora AS date) BETWEEN @FechaDesde AND @FechaHasta);
    DECLARE @dw bigint = (
        SELECT ISNULL(SUM(h.CANTIDAD_LLAMADAS), 0)
        FROM DW.Hechos_Atencion h
        INNER JOIN DW.Dimension_Tiempo t ON t.FECHAKEY = h.FECHAKEY
        WHERE t.FECHA BETWEEN @FechaDesde AND @FechaHasta);

    DECLARE @sinGeo bigint = (
        SELECT ISNULL(SUM(h.CANTIDAD_LLAMADAS), 0)
        FROM DW.Hechos_Atencion h
        INNER JOIN DW.Dimension_Tiempo t ON t.FECHAKEY = h.FECHAKEY
        WHERE t.FECHA BETWEEN @FechaDesde AND @FechaHasta
          AND h.COD_ZONAKEY = @zonaND);

    PRINT CONCAT(N'Hechos: ', @filasBorradas, N' borrados, ', @filasInsertadas, N' insertados. ',
                 N'Staging en rango = ', @stg, N' | DW en rango = ', CONVERT(bigint, @dw),
                 N' | sin georreferencia = ', @sinGeo,
                 N' (', CAST(100.0 * @sinGeo / NULLIF(@dw, 0) AS decimal(5,2)), N' %).');

    IF @Validar = 1 AND @stg <> CONVERT(bigint, @dw)
    BEGIN
        SET @msg = CONCAT(N'RECONCILIACION FALLIDA: staging=', @stg, N' vs DW=', CONVERT(bigint, @dw),
                          N' (diferencia=', @stg - CONVERT(bigint, @dw), N').');
        THROW 50002, @msg, 1;
    END

    SELECT @filasInsertadas       AS HechosInsertados,
           @stg                   AS IncidentesEnStaging,
           CONVERT(bigint, @dw)   AS IncidentesEnDW,
           @sinGeo                AS SinGeorreferencia,
           CASE WHEN @stg = CONVERT(bigint, @dw) THEN 'OK' ELSE 'FALLA' END AS Reconciliacion;
END;
GO

PRINT 'dbo.Usp_Cargar_Hechos_DesdeStaging creado (v2: mapas de dimension sin duplicados).';
GO
