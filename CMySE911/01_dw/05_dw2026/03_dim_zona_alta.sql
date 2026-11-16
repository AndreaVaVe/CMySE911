/* ============================================================================
   DW2026 · PASO 3 de 5 · Altas en DW.Dimension_Zona
   ----------------------------------------------------------------------------
   El DW une los hechos por DISTRITO_ID. La fuente nueva no trae ese dato: la
   zona sale de la coordenada y llega como NOMBRE de barrio. De los 74 barrios
   de la fuente, 16 no existen en Dimension_Zona; sin darlos de alta, el JOIN
   los descartaria en silencio y desaparecerian del cubo.

   Se agregan con un DISTRITO_ID sintetico (9001-9016) para no chocar con los
   reales (maximo actual: 176). Se respeta el patron SCD2: FECHA_FINAL 9999-12-31.

   Ademas se asegura la fila centinela 'SIN GEORREFERENCIA', que recibe todos
   los incidentes sin coordenada (59,46 % del total).

   Idempotente: solo inserta lo que falta.
   ============================================================================ */

USE DW2026;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

BEGIN TRANSACTION;

/* --- 0. Comprobar que los DISTRITO_ID sinteticos no chocan con los reales --- */
IF EXISTS (SELECT 1 FROM DW.Dimension_Zona WHERE DISTRITO_ID BETWEEN 9000 AND 9016)
    PRINT 'Aviso: ya existen DISTRITO_ID en el rango sintetico 9000-9016. Se respetaran los existentes.';

/* --- 1. Fila centinela para los incidentes sin coordenada ------------------ */
IF NOT EXISTS (SELECT 1 FROM DW.Dimension_Zona
               WHERE ZONA = N'SIN GEORREFERENCIA' AND FECHA_FINAL = '9999-12-31')
BEGIN
    INSERT INTO DW.Dimension_Zona (DISTRITO_ID, ZONA, FECHA_INICIAL, FECHA_FINAL)
    VALUES (9000, N'SIN GEORREFERENCIA', '2018-01-01', '9999-12-31');
    PRINT 'Alta: SIN GEORREFERENCIA (DISTRITO_ID 9000)';
END
ELSE
    PRINT 'Ya existe: SIN GEORREFERENCIA';

/* --- 2. Los 16 barrios que la fuente nueva usa y el DW no tenia ------------ */
DECLARE @barrios TABLE (DistritoID int PRIMARY KEY, Zona varchar(100) NOT NULL);

INSERT INTO @barrios (DistritoID, Zona) VALUES
    (9001, '1 DE MARZO'),
    (9002, '12 DE OCTUBRE'),
    (9003, 'ALBERDI'),
    (9004, 'Azopardo'),
    (9005, 'BARRIO PRIVADO CAMPO LAS MORAS'),
    (9006, 'CUCHARITA'),
    (9007, 'EPAM 3'),
    (9008, 'GUERRERO'),
    (9009, 'KENNEDY'),
    (9010, 'LOS CEIBOS'),
    (9011, 'LOZANO'),
    (9012, 'LUJAN'),
    (9013, 'PRENSA'),
    (9014, 'SAN CAYETANO'),
    (9015, 'SANTA RITA'),
    (9016, 'SANTA ROSA');

INSERT INTO DW.Dimension_Zona (DISTRITO_ID, ZONA, FECHA_INICIAL, FECHA_FINAL)
SELECT b.DistritoID, b.Zona, '2018-01-01', '9999-12-31'
FROM @barrios b
WHERE NOT EXISTS (
    SELECT 1 FROM DW.Dimension_Zona z
    WHERE z.ZONA = b.Zona AND z.FECHA_FINAL = '9999-12-31'
)
AND NOT EXISTS (
    SELECT 1 FROM DW.Dimension_Zona z WHERE z.DISTRITO_ID = b.DistritoID
);

PRINT CONCAT('Barrios nuevos insertados: ', @@ROWCOUNT, ' de 16.');

COMMIT TRANSACTION;
GO

/* --- 3. Comprobacion: los 74 barrios de la fuente deben resolver ------------- */
SELECT
    COUNT(*)                                                    AS ZonasActivas,
    SUM(CASE WHEN ZONA = 'SIN GEORREFERENCIA' THEN 1 ELSE 0 END) AS TieneCentinela,
    SUM(CASE WHEN DISTRITO_ID >= 9000 THEN 1 ELSE 0 END)         AS Sinteticas
FROM DW.Dimension_Zona
WHERE FECHA_FINAL = '9999-12-31';
GO
