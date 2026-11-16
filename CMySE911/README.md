# CMySE 911 · Sistema de analítica de datos

Tesis para el título de **Magíster en Ciencia de Datos** (Universidad Católica de Salta):
*«Optimización del sistema de respuesta a emergencias del Centro de Monitoreo y Sistema de Emergencia
911 de San Salvador de Jujuy mediante técnicas de análisis de datos»*.

## Contenido

| Carpeta | Qué contiene |
|---|---|
| `01_dw/` | Solución `DW2026.sln` con los proyectos `ETL2023` y `CUBO2023`, los guiones de área de tránsito y carga, y la carpeta de respaldo |
| `02_modelo/` | Cuaderno del modelo oficial, cuaderno de demostración, guiones de la serie mensual y series de entrada |
| `03_sig/` | Guiones de normalización, análisis espacial y cartografía, más el proyecto QGIS |
| `04_tesis/` | Documento final en `.docx` y `.pdf`, y las respuestas de la evaluación de usabilidad |

## 1. Restaurar la base de datos

El Data Warehouse no se distribuye dentro del repositorio: pesa más de 1,5 GB y GitHub rechaza archivos
de más de 100 MB. Se publica como **asset de un Release** (`DW2026_final.bak`).

### 1.1 Descargar y ubicar

Descargar `DW2026_final.bak` desde la sección **Releases** del repositorio y guardarlo en una carpeta
local, por ejemplo `C:\DWDATA\BACKUP\DW2026_final.bak`.

Crear estas carpetas antes de restaurar, porque el respaldo contiene varios archivos:

```
C:\DWDATA\MDF\
C:\DWDATA\LDF\
```

### 1.2 Ver los archivos que contiene el respaldo

```sql
RESTORE FILELISTONLY
FROM DISK = 'C:\DWDATA\BACKUP\DW2026_final.bak';
```

Devuelve una fila por archivo lógico. Hay que anotar la columna `LogicalName` de cada una: son unas
doce, entre la base, los ocho archivos de hechos por año, el de dimensiones, el de tránsito y el log.

### 1.3 Restaurar

El respaldo trae los archivos originales en rutas que no existen en otra máquina, así que hay que
redirigirlos con `MOVE`. Reemplazar la ruta del respaldo y repetir una cláusula `MOVE` por cada
`LogicalName` que devolvió el paso anterior:

```sql
RESTORE DATABASE DW2026
FROM DISK = 'C:\DWDATA\BACKUP\DW2026_final.bak'
WITH REPLACE, RECOVERY,
     MOVE 'DW2026'             TO 'C:\DWDATA\MDF\DW2026.mdf',
     MOVE 'DW2026_LOG'         TO 'C:\DWDATA\LDF\DW2026_LOG.ldf',
     MOVE 'DW2026_DIMENSIONS'  TO 'C:\DWDATA\MDF\DW2026_DIMENSIONS.ndf',
     MOVE 'DW2026_STAGING'     TO 'C:\DWDATA\MDF\DW2026_STAGING.ndf',
     MOVE 'DW2026_FACTS2018'   TO 'C:\DWDATA\MDF\DW2026_FACTS2018.ndf',
     MOVE 'DW2026_FACTS2019'   TO 'C:\DWDATA\MDF\DW2026_FACTS2019.ndf',
     MOVE 'DW2026_FACTS2020'   TO 'C:\DWDATA\MDF\DW2026_FACTS2020.ndf',
     MOVE 'DW2026_FACTS2021'   TO 'C:\DWDATA\MDF\DW2026_FACTS2021.ndf',
     MOVE 'DW2026_FACTS2022'   TO 'C:\DWDATA\MDF\DW2026_FACTS2022.ndf',
     MOVE 'DW2026_FACTS2023'   TO 'C:\DWDATA\MDF\DW2026_FACTS2023.ndf',
     MOVE 'DW2026_FACTS2024'   TO 'C:\DWDATA\MDF\DW2026_FACTS2024.ndf',
     MOVE 'DW2026_FACTS2025'   TO 'C:\DWDATA\MDF\DW2026_FACTS2025.ndf';
```

Si algún nombre no coincide, el motor devuelve el error correspondiente y hay que ajustarlo con el
`LogicalName` exacto del paso 1.2.

### 1.4 Verificar la restauración

```sql
USE DW2026;
SELECT COUNT(*) AS filas_hecho FROM dbo.FactIncidente;          -- esperado: 1.100.708
SELECT COUNT(*) AS zonas FROM dbo.DimZona WHERE EsActual = 1;   -- esperado: 194
SELECT COUNT(*) AS clasificadores FROM dbo.DimClasificador;     -- esperado: 132
```

Si el total no es 1.100.708, la restauración quedó incompleta: revisar que se hayan movido todos los
archivos del paso 1.3.

## 2. Abrir la solución y ajustar la conexión

1. Abrir `01_dw/DW2026.sln` con Visual Studio 2022, con las extensiones de **Integration Services** y
   **Analysis Services** instaladas.
2. En `ETL2023`, abrir cada administrador de conexiones (`CMySE911.conmgr` y `DW2026.conmgr`) y
   reemplazar los marcadores:

   | Marcador | Reemplazar por |
   |---|---|
   | `{SERVIDOR}` | Nombre de la instancia, por ejemplo `.` o `localhost` |
   | `{USUARIO}` | Usuario de base de datos, o elegir autenticación de Windows |

3. Apretar **Probar conexión** en cada uno. Los errores de validación del paquete desaparecen cuando
   ambas conexiones responden.
4. Compilar el proyecto: clic derecho en `ETL2023` → **Compilar**. Se genera el `.ispac` que SSIS
   necesita para ejecutar.

## 3. Procesar el cubo

El orden importa: **primero las dimensiones, después el cubo.** Procesar el cubo antes que las
dimensiones produce el error de clave de atributo no encontrada. Se puede hacer desde Visual Studio o
con los guiones incluidos:

```powershell
# 1) procesar las cuatro dimensiones en modo completo
.\01_dw\05_dw2026\procesar_cubo.ps1 -SoloDimensiones

# 2) procesar el cubo
.\01_dw\05_dw2026\procesar_cubo.ps1 -Cubo
```

Si se hace desde Visual Studio: clic derecho en el cubo → **Procesar** → modo **Completo**, y repetir
para cada dimensión antes de procesar el cubo.

## 4. Verificar el cubo contra la tabla de hechos

```powershell
.\01_dw\05_dw2026\verificar_cubo_mdx.ps1
```

El guion consulta el cubo por MDX y compara el total con `dbo.FactIncidente`. Los dos valores deben
coincidir en **1.100.708**.

También se puede comprobar a mano en SSMS, sobre el cubo desplegado:

```sql
SELECT [Medidas].[Incidentes] ON COLUMNS
FROM [CUBO2023];
```

## 5. Modelo de pronóstico

Hay dos cuadernos, y conviene saber cuál es cuál:

| Cuaderno | Qué hace |
|---|---|
| `02_modelo/Modelo_CMySE911.ipynb` | **El modelo de la tesis.** Estima la retícula de 143 especificaciones, valida sobre 2025, reajusta y proyecta 2026-2027. Reproduce las cifras del capítulo 5 |
| `02_modelo/demo/Demo_Prediccion911.ipynb` | Demostración didáctica del ejercicio exploratorio previo. Sirve para mostrar el procedimiento en Colab; **no reproduce las cifras de la tesis** |

Ambos se suben a [Colab](https://colab.research.google.com) y se ejecutan en orden. El primero pide
`serie_mensual.csv`; el demo pide los tres CSV que están en su carpeta.

Resultado del modelo oficial:

| Métrica | Valor |
|---|---|
| Especificaciones estimadas | 143 |
| Modelo elegido | SARIMAX(2,1,0)(1,1,1)[12] |
| MAE de prueba | 162,76 |
| RMSE de prueba | 213,30 |
| MAPE de prueba | 1,96 % |
| MASE | 0,20 |
| Proyección 2026 | **94.380 incidentes** |
| Ljung-Box a 12 rezagos | 17,591 · p = 0,129 |

El intervalo del total anual, por simulación de 20.000 trayectorias, va de 50.313 a 137.685.

## 6. QGIS

`03_sig/CMySE911.qgz` trae los estilos embebidos. Las rutas de las capas quedaron como
`C:\Users\{USUARIO}\CMySE911\...`: al abrirlo hay que reconectar las capas con la opción de reparar del
navegador.

## Qué no está incluido

- **Los archivos de origen del sistema transaccional.** Contienen registros del centro y no
  corresponden a este trabajo distribuirlos.
- La base `CMySE911` de origen. Por la misma razón.
- Las capas geográficas intermedias (`.gpkg`, `.tif`) y el atlas en PDF: se regeneran con los guiones
  de `03_sig/`.

El paquete ETL queda como documentación de cómo se construyó el repositorio: sin la base de origen no
puede ejecutarse de punta a punta. Lo que se verifica con el respaldo es el resultado: la tabla de
hechos, las dimensiones y el cubo.

## Capa de presentación

El tablero se implementó en Looker Studio, conectado al Data Warehouse con un extracto preagregado, y
se aloja en el espacio institucional. No se distribuye en este repositorio.
