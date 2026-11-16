/* ============================================================================
   DW2026 · PASO 1 de 5 · Tabla de staging para la fuente nueva
   ----------------------------------------------------------------------------
   No crea ninguna base de datos: trabaja sobre DW2026, que ya existe.
   Solo agrega una tabla en el esquema stg.

   OJO: el paquete SSIS antiguo (ETL_DW_2023.dtsx) trunca TODAS las tablas base
   del esquema stg. Si alguien vuelve a ejecutar ese paquete, esta tabla se
   vacia. No es destructivo —solo hay que recargarla— pero conviene saberlo.

   Idempotente.
   ============================================================================ */

USE DW2026;
GO
SET NOCOUNT ON;
GO

IF SCHEMA_ID('stg') IS NULL
    EXEC('CREATE SCHEMA stg');
GO

IF OBJECT_ID('stg.imp_incidente') IS NULL
CREATE TABLE stg.imp_incidente
(
    IdOrigen        int           NOT NULL,
    FechaHora       datetime2(0)  NOT NULL,
    CodigoID        tinyint       NOT NULL,
    ColorCod        char(1)       NOT NULL,
    ClasificadorID  int           NOT NULL,
    Barrio          varchar(100)  NULL,     -- vacio = sin georreferencia
    Latitud         decimal(9,7)  NULL,
    Longitud        decimal(9,7)  NULL
);
GO

-- Indice de apoyo para la agregacion y para los controles de calidad.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_imp_incidente_fecha' AND object_id = OBJECT_ID('stg.imp_incidente'))
    CREATE INDEX IX_imp_incidente_fecha ON stg.imp_incidente (FechaHora)
        INCLUDE (CodigoID, ClasificadorID, Barrio);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_imp_incidente_clasif' AND object_id = OBJECT_ID('stg.imp_incidente'))
    CREATE INDEX IX_imp_incidente_clasif ON stg.imp_incidente (ClasificadorID, CodigoID);
GO

PRINT 'stg.imp_incidente lista.';
GO
