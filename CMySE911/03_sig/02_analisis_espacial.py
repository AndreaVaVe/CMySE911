# -*- coding: utf-8 -*-
"""
02_analisis_espacial.py
=======================
Analisis espacial e inteligencia operativa sobre el GeoPackage maestro del
CMySE 911. Se ejecuta en modo headless con el motor de Processing de QGIS.

Encadena algoritmos NATIVOS de QGIS (no requiere scikit-learn, que no esta
instalado en este entorno):

  1. KDE / heatmap            qgis:heatmapkerneldensityestimation
  2. DBSCAN                   native:dbscanclustering
  3. K-Means                  native:kmeansclustering
  4. Conteo por jurisdiccion  qgis:countpointsinpolygon
  5. Estadistica zonal        native:zonalstatisticsfb
  6. Zonas calientes          native:dissolve + native:concavehullbyfeature
  7. Comisaria mas cercana    native:joinbynearest

NOTA DE INGENIERIA
------------------
El motor de Processing NO interpreta la sintaxis "salida.gpkg|layername=x" en
el parametro OUTPUT: la trata como nombre de archivo y produce rutas invalidas.
Por eso cada algoritmo escribe su propio GeoPackage en 02_analisis\\derivados\\
y al final se consolidan todos con gdal.VectorTranslate en un unico
CMySE911_analisis.gpkg. Ventaja adicional: los intermedios quedan disponibles
para inspeccion o reejecucion parcial.

Salidas:
  - 02_analisis\\CMySE911_analisis.gpkg   (capas derivadas consolidadas)
  - 02_analisis\\derivados\\*.gpkg         (resultado crudo de cada algoritmo)
  - 02_analisis\\kde_*.tif                 (superficies de densidad)
  - 05_informes\\ANALISIS_operativo.md + CSV + graficos PNG
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import sqlite3
import sys
from collections import Counter, defaultdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication  # noqa: E402

# El lanzador python-qgis-ltr.bat expone apps\qgis-ltr\python pero NO el
# subdirectorio plugins, que es donde vive el modulo 'processing'. Sin este
# ajuste, "import processing" falla en modo headless (QGIS solo lo agrega en
# la consola grafica).
_PREFIJO = os.environ.get("QGIS_PREFIX_PATH",
                          r"C:\Program Files\QGIS 3.44.11\apps\qgis-ltr")
for _cand in (os.path.join(_PREFIJO, "python", "plugins"),
              os.path.join(_PREFIJO, "python")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)

qgs = QgsApplication([], False)
qgs.initQgis()

from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()
import processing  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402

# --------------------------------------------------------------------------
# CONFIGURACION
# --------------------------------------------------------------------------
# Raiz del proyecto calculada desde la ubicacion del script (ver nota en
# 01_normalizar_911.py): el proyecto es reubicable.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAESTRO = os.path.join(RAIZ, "01_gpkg", "CMySE911_maestro.gpkg")
DIR_AN = os.path.join(RAIZ, "02_analisis")
DIR_DERIV = os.path.join(DIR_AN, "derivados")
GPKG_AN = os.path.join(DIR_AN, "CMySE911_analisis.gpkg")
KDE_TIF = os.path.join(DIR_AN, "kde_incidentes.tif")
DIR_INFORMES = os.path.join(RAIZ, "05_informes")

# Parametros de analisis (unidades del CRS proyectado = metros)
KDE_RADIO_M = 500          # radio de influencia de cada incidente
KDE_PIXEL_M = 50           # resolucion del raster de salida
DBSCAN_EPS_M = 250         # distancia maxima entre puntos del mismo cluster
DBSCAN_MIN_PTS = 8         # tamano minimo de cluster
DBSCAN_BORDER_NOISE = True # True = los puntos borde no se asignan (DBSCAN*)
KMEANS_K = 12              # cantidad de grupos territoriales para reparto de recursos
CONCAVE_ALPHA = 0.35       # 0 = muy ajustado, 1 = convex hull

# Fiabilidad de la densidad por zona. Un cluster de pocos incidentes produce un
# poligono-astilla y una densidad numericamente absurda: el cluster 25 (3
# incidentes, area ~0) daba 10.415 inc/km2. Por debajo de estos minimos la
# densidad NO se usa para priorizar, aunque se conserva en la capa y en el informe.
MIN_INCIDENTES_ZONA = 10
AREA_MINIMA_KM2 = 0.005

# --------------------------------------------------------------------------
# SOBRESCRITURA POR CONFIGURACION EXTERNA
# --------------------------------------------------------------------------
# Los parametros de analisis se ajustan sin editar el script: ver
# 04_scripts\config_911.json. Cambiar DBSCAN_EPS_M o KMEANS_K cambia el
# resultado operativo, asi que conviene documentar el valor usado en el informe
# (el informe markdown lo registra automaticamente).
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_911.json")
if os.path.exists(CONFIG):
    try:
        import json
        with open(CONFIG, encoding="utf-8") as _fh:
            _cfg = json.load(_fh)
        KDE_RADIO_M = _cfg.get("kde_radio_m", KDE_RADIO_M)
        KDE_PIXEL_M = _cfg.get("kde_pixel_m", KDE_PIXEL_M)
        DBSCAN_EPS_M = _cfg.get("dbscan_eps_m", DBSCAN_EPS_M)
        DBSCAN_MIN_PTS = _cfg.get("dbscan_min_pts", DBSCAN_MIN_PTS)
        KMEANS_K = _cfg.get("kmeans_k", KMEANS_K)
        CONCAVE_ALPHA = _cfg.get("concave_alpha", CONCAVE_ALPHA)
        UMBRAL_BRECHA_M = _cfg.get("umbral_brecha_recursos_m", 2000)
        print(f"[config] cargado {CONFIG}")
        print(f"[config] KDE radio={KDE_RADIO_M} m | DBSCAN eps={DBSCAN_EPS_M} m "
              f"min={DBSCAN_MIN_PTS} | K-Means k={KMEANS_K}")
    except Exception as _exc:  # noqa: BLE001
        print(f"[config] IGNORADO por error: {type(_exc).__name__}: {_exc}")
else:
    UMBRAL_BRECHA_M = 2000

# Capas derivadas que se consolidan en el GeoPackage final
CAPAS_DERIVADAS = [
    "incidentes_dbscan",
    "incidentes_kmeans",
    "jurisdiccion_conteo",
    "jurisdiccion_kde",
    "zonas_calientes",
    "zonas_calientes_recursos",
    "incidentes_con_comisaria",
]

pasos: list[dict] = []
RUIDO_DBSCAN = 0   # puntos que DBSCAN deja sin agrupar


def log(msg: str) -> None:
    print(msg, flush=True)


def ruta_capa(nombre: str) -> str:
    """Capa de entrada dentro del GeoPackage maestro."""
    return f"{MAESTRO}|layername={nombre}"


def ruta_deriv(nombre: str) -> str:
    """Archivo de salida individual para un algoritmo."""
    return os.path.join(DIR_DERIV, nombre + ".gpkg")


def ejecutar(etiqueta: str, alg: str, params: dict, salida_clave: str = "OUTPUT"):
    """Ejecuta un algoritmo de Processing registrando exito o fallo."""
    log(f"  -> {etiqueta}")
    try:
        res = processing.run(alg, params)
        valor = res.get(salida_clave)
        pasos.append({"paso": etiqueta, "algoritmo": alg, "estado": "OK",
                      "salida": os.path.basename(str(valor))[:80] if valor else ""})
        return res
    except Exception as exc:  # noqa: BLE001
        pasos.append({"paso": etiqueta, "algoritmo": alg, "estado": "FALLO",
                      "salida": f"{type(exc).__name__}: {exc}"[:200]})
        log(f"     FALLO: {type(exc).__name__}: {str(exc)[:180]}")
        return None


def consolidar():
    """Une todos los GeoPackage derivados en uno solo, con nombres de capa limpios."""
    log("\n[7/8] Consolidacion en GeoPackage unico")
    if os.path.exists(GPKG_AN):
        ogr.GetDriverByName("GPKG").DeleteDataSource(GPKG_AN)
    primero = True
    n = 0
    for capa in CAPAS_DERIVADAS:
        origen = ruta_deriv(capa)
        if not os.path.exists(origen):
            continue
        opciones = ["-f", "GPKG", "-nln", capa,
                    "-lco", "GEOMETRY_NAME=geom", "-lco", "SPATIAL_INDEX=YES"]
        if not primero:
            opciones.append("-update")
        try:
            gdal.VectorTranslate(GPKG_AN, origen, options=opciones)
            n += 1
            primero = False
        except Exception as exc:  # noqa: BLE001
            log(f"     no se pudo consolidar {capa}: {type(exc).__name__}: {exc}")
    log(f"     {n} capas consolidadas en {os.path.basename(GPKG_AN)}")


# --------------------------------------------------------------------------
# 1. DENSIDAD (KDE)
# --------------------------------------------------------------------------
def _distintos_peso():
    """Valores distintos de 'cantidad_llamadas' en el maestro, y total de filas.

    Correccion de un error de la version anterior: el informe declaraba un KDE
    "ponderado por cantidad_llamadas", pero en el lote 2022 ese campo vale 1 en
    TODOS los registros. Ponderar por una constante no cambia el resultado: la
    ponderacion era inerte y el informe afirmaba algo que no ocurria.
    """
    try:
        con = sqlite3.connect(f"file:{MAESTRO}?mode=ro", uri=True)
        n = con.execute(
            "SELECT COUNT(DISTINCT cantidad_llamadas) FROM incidentes").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM incidentes").fetchone()[0]
        con.close()
        return n, total
    except sqlite3.Error:
        return 0, 0


def _hay_peso():
    return _distintos_peso()[0] > 1


def _nota_peso():
    n, total = _distintos_peso()
    if n <= 1:
        return f"sin ponderar: cantidad_llamadas es constante en los {total} registros"
    return f"ponderado por cantidad_llamadas ({n} valores distintos)"


def paso_kde():
    log("\n[1/8] Superficie de densidad KDE")
    ejecutar(
        f"KDE global (radio {KDE_RADIO_M} m, pixel {KDE_PIXEL_M} m)",
        "qgis:heatmapkerneldensityestimation",
        {
            "INPUT": ruta_capa("incidentes"),
            "RADIUS": KDE_RADIO_M,
            "PIXEL_SIZE": KDE_PIXEL_M,
            **( {"WEIGHT_FIELD": "cantidad_llamadas"} if _hay_peso() else {} ),
            "OUTPUT": KDE_TIF,
        },
    )

    # KDE por clasificador: permite comparar patrones, no solo volumen
    conteo = Counter()
    for r in leer_ogr(MAESTRO, "incidentes", ["clasificador"]):
        conteo[r["clasificador"]] += 1

    for clasif, _ in conteo.most_common(3):
        seguro = "".join(ch if ch.isalnum() else "_" for ch in clasif.lower())[:40]
        tmp = os.path.join(DIR_DERIV, f"_filtro_{seguro}.gpkg")
        ejecutar(
            f"filtro clasificador = {clasif}",
            "native:extractbyexpression",
            {
                "INPUT": ruta_capa("incidentes"),
                "EXPRESSION": f"\"clasificador\" = '{clasif}'",
                "OUTPUT": tmp,
            },
        )
        ejecutar(
            f"KDE para {clasif}",
            "qgis:heatmapkerneldensityestimation",
            {
                "INPUT": tmp,
                "RADIUS": KDE_RADIO_M,
                "PIXEL_SIZE": KDE_PIXEL_M,
                "OUTPUT": os.path.join(DIR_AN, f"kde_clasificador_{seguro}.tif"),
            },
        )


# --------------------------------------------------------------------------
# 2. CLUSTERING
# --------------------------------------------------------------------------
def paso_clustering():
    global RUIDO_DBSCAN
    log("\n[2/8] Clustering espacial")
    res = ejecutar(
        f"DBSCAN (eps={DBSCAN_EPS_M} m, min={DBSCAN_MIN_PTS})",
        "native:dbscanclustering",
        {
            "INPUT": ruta_capa("incidentes"),
            "MIN_SIZE": DBSCAN_MIN_PTS,
            "EPS": DBSCAN_EPS_M,
            "FIELD_NAME": "cluster_db",
            "SIZE_FIELD_NAME": "cluster_n",
            "OUTPUT": ruta_deriv("incidentes_dbscan"),
        },
    )
    if res and res.get("NUM_CLUSTERS") is not None:
        log(f"     clusters detectados: {res['NUM_CLUSTERS']}")

    # Puntos sin agrupar: DBSCAN los deja SIN numero de cluster (NULL), no en -1.
    # Se cuentan aca para que el informe y el pie del mapa usen el dato real en
    # lugar de un numero escrito a mano.
    ds = ogr.Open(ruta_deriv("incidentes_dbscan"), 0)
    if ds is not None:
        lyr = ds.GetLayer(0)
        for f in lyr:
            c = f.GetField("cluster_db")
            if c is None or c < 0:
                RUIDO_DBSCAN += 1
        ds = None
    log(f"     puntos sin agrupar (ruido): {RUIDO_DBSCAN}")

    ejecutar(
        f"K-Means (k={KMEANS_K}) para reparto de recursos",
        "native:kmeansclustering",
        {
            "INPUT": ruta_capa("incidentes"),
            "CLUSTERS": KMEANS_K,
            "FIELD_NAME": "grupo_kmeans",
            "SIZE_FIELD_NAME": "grupo_n",
            "OUTPUT": ruta_deriv("incidentes_kmeans"),
        },
    )


# --------------------------------------------------------------------------
# 3. AGREGACION POR JURISDICCION + ESTADISTICA ZONAL
# --------------------------------------------------------------------------
def paso_agregacion():
    log("\n[3/8] Agregacion por jurisdiccion")
    ejecutar(
        "conteo de incidentes por poligono de jurisdiccion",
        "qgis:countpointsinpolygon",
        {
            "POLYGONS": ruta_capa("jurisdicciones_ur"),
            "POINTS": ruta_capa("incidentes"),
            "WEIGHT": "cantidad_llamadas",
            "FIELD": "n_incidentes",
            "OUTPUT": ruta_deriv("jurisdiccion_conteo"),
        },
    )
    if os.path.exists(KDE_TIF):
        ejecutar(
            "estadistica zonal del KDE por jurisdiccion",
            "native:zonalstatisticsfb",
            {
                "INPUT": ruta_deriv("jurisdiccion_conteo"),
                "INPUT_RASTER": KDE_TIF,
                "RASTER_BAND": 1,
                "COLUMN_PREFIX": "kde_",
                "STATISTICS": [0, 2, 6],
                "OUTPUT": ruta_deriv("jurisdiccion_kde"),
            },
        )
    else:
        log("     (se omite: no hay raster KDE)")


# --------------------------------------------------------------------------
# 4. POLIGONOS DE ZONAS CALIENTES
# --------------------------------------------------------------------------
def paso_zonas_calientes():
    log("\n[4/8] Poligonos de zonas calientes")
    ejecutar(
        "disolucion de puntos por cluster DBSCAN",
        "native:dissolve",
        {
            "INPUT": ruta_deriv("incidentes_dbscan"),
            "FIELD": ["cluster_db"],
            "SEPARATE_DISJOINT": False,
            "OUTPUT": ruta_deriv("clusters_disolucion"),
        },
    )
    ejecutar(
        f"concave hull por cluster (alpha={CONCAVE_ALPHA})",
        "native:concavehullbyfeature",
        {
            "INPUT": ruta_deriv("clusters_disolucion"),
            "ALPHA": CONCAVE_ALPHA,
            "HOLES": False,
            "OUTPUT": ruta_deriv("zonas_calientes"),
        },
    )
    borrados = limpiar_zonas_calientes()
    if borrados:
        log(f"     descartado(s) {borrados} poligono(s) de la clase sin cluster (ruido)")


def limpiar_zonas_calientes():
    """Elimina de las zonas calientes el poligono del grupo sin cluster.

    El dissolve genera un grupo con cluster_db NULL (los puntos que DBSCAN dejo
    sin agrupar). Al pasar por el concave hull producia un poligono que NO es una
    zona caliente real y que ademas se rotulaba en la leyenda con el rango de
    cantidad de incidentes, dando una lectura falsa del mapa.
    """
    ruta = ruta_deriv("zonas_calientes")
    ds = ogr.Open(ruta, 1)
    if ds is None:
        return 0
    lyr = ds.GetLayer(0)
    borrados = 0
    for f in list(lyr):
        c = f.GetField("cluster_db")
        if c is None or c < 0:
            lyr.DeleteFeature(f.GetFID())
            borrados += 1
    ds = None
    return borrados


# --------------------------------------------------------------------------
# 5. DISTANCIA A RECURSOS
# --------------------------------------------------------------------------
def paso_recursos():
    log("\n[5/8] Distancia a recursos operativos")
    ejecutar(
        "comisaria mas cercana para cada incidente",
        "native:joinbynearest",
        {
            "INPUT": ruta_deriv("incidentes_dbscan"),
            "INPUT_2": ruta_capa("puntos_operativos"),
            "FIELDS_TO_COPY": ["denominacion", "ur"],
            "PREFIX": "cria_",
            "NEIGHBORS": 1,
            "DISCARD_NONMATCHING": False,
            "OUTPUT": ruta_deriv("incidentes_con_comisaria"),
        },
    )
    ejecutar(
        "comisaria mas cercana para cada zona caliente",
        "native:joinbynearest",
        {
            "INPUT": ruta_deriv("zonas_calientes"),
            "INPUT_2": ruta_capa("puntos_operativos"),
            "FIELDS_TO_COPY": ["denominacion"],
            "PREFIX": "cria_",
            "NEIGHBORS": 1,
            "DISCARD_NONMATCHING": False,
            "OUTPUT": ruta_deriv("zonas_calientes_recursos"),
        },
    )


# --------------------------------------------------------------------------
# LECTURA Y AGREGACIONES
# --------------------------------------------------------------------------
def leer_ogr(ruta, capa, campos):
    ds = ogr.Open(ruta, 0)
    if ds is None:
        return []
    lyr = ds.GetLayerByName(capa)
    if lyr is None:
        ds = None
        return []
    filas = []
    for f in lyr:
        filas.append({c: f.GetField(c) for c in campos})
    ds = None
    return filas


def campos_de(ruta, capa):
    ds = ogr.Open(ruta, 0)
    if ds is None:
        return []
    lyr = ds.GetLayerByName(capa)
    if lyr is None:
        ds = None
        return []
    nombres = [lyr.GetLayerDefn().GetFieldDefn(i).GetName()
               for i in range(lyr.GetLayerDefn().GetFieldCount())]
    ds = None
    return nombres


def enriquecer_zonas(ruta_gpkg, por_cluster, areas):
    """Escribe en la capa zonas_calientes las metricas derivadas del analisis.

    Sin esto QGIS solo dispone de cluster_db y cluster_n: no puede simbolizar por
    densidad ni etiquetar la carga sin leer el CSV del informe, y la capa y el
    informe podrian divergir. Ademas deja explicito que zonas tienen densidad
    fiable, para poder filtrarlas en el mapa.
    """
    campos = [
        ("incidentes", ogr.OFTInteger),
        ("area_km2", ogr.OFTReal),
        ("densidad_km2", ogr.OFTReal),
        ("fiable_densidad", ogr.OFTInteger),
        ("dist_media_dep_m", ogr.OFTReal),
        ("dependencia", ogr.OFTString),
        ("zona_predominante", ogr.OFTString),
    ]
    ds = ogr.Open(ruta_gpkg, 1)
    if ds is None:
        avisos.append("no se pudo abrir el GeoPackage para enriquecer las zonas")
        return 0
    lyr = ds.GetLayerByName("zonas_calientes")
    if lyr is None:
        ds = None
        return 0
    existentes = [lyr.GetLayerDefn().GetFieldDefn(i).GetName()
                  for i in range(lyr.GetLayerDefn().GetFieldCount())]
    for nombre, tipo in campos:
        if nombre not in existentes:
            fd = ogr.FieldDefn(nombre, tipo)
            if tipo == ogr.OFTString:
                fd.SetWidth(64)
            lyr.CreateField(fd)

    # Se recolectan los valores primero y se escriben despues: modificar features
    # mientras se itera la misma capa es fragil en OGR.
    valores = {}
    for f in lyr:
        c = f.GetField("cluster_db")
        d = por_cluster.get(c)
        n = d["n"] if d else 0
        area = areas.get(c) or 0.0
        dist = d["dist"] if d and d["dist"] else []
        valores[f.GetFID()] = {
            "incidentes": n,
            "area_km2": round(area, 4),
            "densidad_km2": round((n / area) if area > 0 else 0.0, 1),
            "fiable_densidad": 1 if (n >= MIN_INCIDENTES_ZONA
                                     and area >= AREA_MINIMA_KM2) else 0,
            "dist_media_dep_m": round(sum(dist) / len(dist), 1) if dist else None,
            "dependencia": (d["cria"].most_common(1)[0][0] if d and d["cria"] else ""),
            "zona_predominante": (d["zonas"].most_common(1)[0][0]
                                  if d and d["zonas"] else ""),
        }
    fiables = 0
    for fid, v in valores.items():
        f = lyr.GetFeature(fid)
        for nombre, valor in v.items():
            f.SetField(nombre, valor)
        lyr.SetFeature(f)
        f = None
        fiables += v["fiable_densidad"]

    # Limpieza: el dissolve arrastra los atributos del primer punto de cada
    # cluster (fecha, clasificador, coordenadas, metadatos del lote). En una capa
    # de zonas no aportan nada y ensucian la tabla de atributos del informe.
    conservar = {"cluster_db", "cluster_n", "area", "perimeter",
                 "incidentes", "area_km2", "densidad_km2", "fiable_densidad",
                 "dist_media_dep_m", "dependencia", "zona_predominante"}
    eliminados = 0
    for i in range(lyr.GetLayerDefn().GetFieldCount() - 1, -1, -1):
        nombre = lyr.GetLayerDefn().GetFieldDefn(i).GetName()
        if nombre in conservar:
            continue
        try:
            lyr.DeleteField(i)
            eliminados += 1
        except Exception:  # noqa: BLE001
            pass
    log(f"     campos heredados eliminados de zonas_calientes: {eliminados}")
    ds = None
    return fiables


def analizar_zonas(ruta_analisis):
    """Densidad real y estructura interna de cada zona (barrio) del sistema 911.

    El campo ZONA mezcla escalas: hay barrios de ~1,5 km2 (MARIANO MORENO,
    1,1x1,3 km) y distritos de ~100 km2 (CAMPO VERDE, 8,7x11,2 km). Comparar
    conteos por zona sin normalizar por superficie hace ver como "la zona mas
    critica" a la mas grande, no a la mas intensa.

    Aca se calcula el area por ENVOLVENTE CONVEXA de los propios incidentes de la
    zona (mas justa que el rectangulo envolvente, aunque tambien la sobreestima en
    formas irregulares), y se separa cuantos incidentes estan en focos densos y
    cuantos dispersos. Ademas detecta las zonas con mas de un foco: dos o mas
    concentraciones dentro de la MISMA etiqueta administrativa, que la zona no
    alcanza a distinguir.
    """
    ds = ogr.Open(ruta_analisis, 0)
    if ds is None:
        return []
    lyr = ds.GetLayerByName("incidentes_con_comisaria")
    if lyr is None:
        ds = None
        return []

    zonas = defaultdict(lambda: {"n": 0, "pts": [], "focos": defaultdict(list)})
    for f in lyr:
        z = f.GetField("zona") or ""
        if not z:
            continue
        x, y = f.GetField("x_posgar"), f.GetField("y_posgar")
        d = zonas[z]
        d["n"] += 1
        d["pts"].append((x, y))
        c = f.GetField("cluster_db")
        if c is not None and c >= 0:
            d["focos"][int(c)].append((x, y))
    ds = None

    filas = []
    for z, d in zonas.items():
        multi = ogr.Geometry(ogr.wkbMultiPoint)
        for x, y in d["pts"]:
            p = ogr.Geometry(ogr.wkbPoint)
            p.AddPoint_2D(x, y)
            multi.AddGeometry(p)
        area = 0.0
        try:
            hull = multi.ConvexHull()
            if hull is not None:
                area = hull.GetArea() / 1e6
        except Exception:  # noqa: BLE001
            area = 0.0

        centros = []
        for c, pts in d["focos"].items():
            centros.append((sum(p[0] for p in pts) / len(pts),
                            sum(p[1] for p in pts) / len(pts)))
        separaciones = []
        for i in range(len(centros)):
            for j in range(i + 1, len(centros)):
                dx = centros[i][0] - centros[j][0]
                dy = centros[i][1] - centros[j][1]
                separaciones.append((dx * dx + dy * dy) ** 0.5)

        n = d["n"]
        en_focos = sum(len(p) for p in d["focos"].values())
        filas.append({
            "zona": z,
            "n": n,
            "en_focos": en_focos,
            "dispersos": n - en_focos,
            "n_focos": len(d["focos"]),
            "area_km2": round(area, 3),
            "densidad_km2": round(n / area, 1) if (area > 0 and n >= MIN_INCIDENTES_ZONA) else "",
            "sep_min_m": round(min(separaciones), 0) if separaciones else "",
            "sep_max_m": round(max(separaciones), 0) if separaciones else "",
        })
    filas.sort(key=lambda r: (r["densidad_km2"] if isinstance(r["densidad_km2"], float) else -1),
               reverse=True)
    return filas


def paso_informes():
    log("\n[6/8] Agregaciones e informes")
    os.makedirs(DIR_INFORMES, exist_ok=True)

    # --- Detecta el nombre real del campo de distancia que agrega joinbynearest
    campos_icc = campos_de(GPKG_AN, "incidentes_con_comisaria")
    campo_dist = "distance" if "distance" in campos_icc else None
    if campo_dist is None:
        for c in campos_icc:
            if "dist" in c.lower():
                campo_dist = c
                break
    log(f"     capas consolidadas; campo de distancia = {campo_dist}")

    # --- Ranking de clusters con distancia a recursos ---
    filas = leer_ogr(GPKG_AN, "incidentes_con_comisaria",
                     [c for c in ("cluster_db", "clasificador", "zona",
                                  "cria_denominacion", campo_dist) if c])
    por_cluster = defaultdict(lambda: {"n": 0, "dist": [], "clasif": Counter(),
                                       "zonas": Counter(), "cria": Counter()})
    for r in filas:
        c = r.get("cluster_db")
        if c is None or c < 0:
            continue
        d = por_cluster[c]
        d["n"] += 1
        if campo_dist and r.get(campo_dist) is not None:
            d["dist"].append(r[campo_dist])
        if r.get("clasificador"):
            d["clasif"][r["clasificador"]] += 1
        if r.get("zona"):
            d["zonas"][r["zona"]] += 1
        if r.get("cria_denominacion"):
            d["cria"][r["cria_denominacion"]] += 1

    # Areas reales de las zonas calientes (EPSG:5345 -> metros)
    areas = {}
    ds = ogr.Open(GPKG_AN, 0)
    lyr = ds.GetLayerByName("zonas_calientes") if ds else None
    if lyr is not None:
        for f in lyr:
            g = f.GetGeometryRef()
            c = f.GetField("cluster_db")
            if g is not None and c is not None:
                areas[c] = g.GetArea() / 1e6  # km2
    ds = None

    # Enriquecer la capa de zonas calientes con las metricas derivadas, para que
    # QGIS pueda simbolizar y etiquetar sin depender del CSV del informe.
    fiables = enriquecer_zonas(GPKG_AN, por_cluster, areas)
    log(f"     zonas de densidad fiable: {fiables}")

    ruta_rank = os.path.join(DIR_INFORMES, "analisis_clusters.csv")
    with open(ruta_rank, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["cluster", "incidentes", "area_km2", "densidad_inc_km2",
                    "dist_media_comisaria_m", "dist_max_comisaria_m",
                    "comisaria_mas_frecuente", "zona_predominante",
                    "clasificador_predominante"])
        ranking = []
        for c, d in por_cluster.items():
            area = areas.get(c) or 0
            dist = d["dist"]
            fila = [
                c, d["n"], round(area, 3),
                round(d["n"] / area, 1) if area > 0 else "",
                round(sum(dist) / len(dist), 1) if dist else "",
                round(max(dist), 1) if dist else "",
                d["cria"].most_common(1)[0][0] if d["cria"] else "",
                d["zonas"].most_common(1)[0][0] if d["zonas"] else "",
                d["clasif"].most_common(1)[0][0] if d["clasif"] else "",
            ]
            w.writerow(fila)
            ranking.append(fila)
        ranking.sort(key=lambda r: r[1], reverse=True)

    # --- Carga de demanda por dependencia policial ---
    # Responde a "que dependencia absorbe mas demanda y a que distancia", que es
    # la pregunta operativa detras de redistribuir recursos.
    por_dep = defaultdict(lambda: {"n": 0, "dist": [], "clasif": Counter()})
    for r in filas:
        dep = r.get("cria_denominacion")
        if not dep:
            continue
        d = por_dep[dep]
        d["n"] += 1
        if campo_dist and r.get(campo_dist) is not None:
            d["dist"].append(r[campo_dist])
        if r.get("clasificador"):
            d["clasif"][r["clasificador"]] += 1

    ranking_dep = []
    for dep, d in por_dep.items():
        dist = d["dist"]
        ranking_dep.append([
            dep, d["n"],
            round(sum(dist) / len(dist), 1) if dist else "",
            d["clasif"].most_common(1)[0][0] if d["clasif"] else "",
        ])
    ranking_dep.sort(key=lambda r: r[1], reverse=True)

    ruta_dep = os.path.join(DIR_INFORMES, "analisis_dependencias.csv")
    with open(ruta_dep, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["dependencia", "incidentes_asignados", "dist_media_m",
                    "clasificador_predominante"])
        for fila in ranking_dep:
            w.writerow(fila)

    # --- Densidad real y estructura interna por zona (barrio) ---
    zonas_info = analizar_zonas(GPKG_AN)
    with open(os.path.join(DIR_INFORMES, "analisis_densidad_zonas.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["zona", "n", "en_focos", "dispersos",
                                           "n_focos", "area_km2", "densidad_km2",
                                           "sep_min_m", "sep_max_m"])
        w.writeheader()
        for fila_z in zonas_info:
            w.writerow(fila_z)
    log(f"     zonas analizadas: {len(zonas_info)} | "
        f"con mas de un foco: {sum(1 for z in zonas_info if z['n_focos'] >= 2)}")

    # --- Agregacion por zona, UR, mes y clasificador ---
    base = leer_ogr(MAESTRO, "incidentes",
                    ["zona", "ur_origen", "clasificador", "fecha"])
    por_zona = Counter(r["zona"] for r in base if r["zona"])
    por_ur = Counter(r["ur_origen"] for r in base)
    por_mes = Counter((r["fecha"] or "")[:7] for r in base if r["fecha"])
    por_clasif = Counter(r["clasificador"] for r in base if r["clasificador"])

    with open(os.path.join(DIR_INFORMES, "analisis_zonas.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["zona", "incidentes"])
        for k, v in por_zona.most_common():
            w.writerow([k, v])

    with open(os.path.join(DIR_INFORMES, "serie_mensual.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mes", "incidentes"])
        for k in sorted(por_mes):
            w.writerow([k, por_mes[k]])

    cruce = Counter((r["zona"], r["clasificador"]) for r in base if r["zona"])
    with open(os.path.join(DIR_INFORMES, "analisis_zona_clasificador.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["zona", "clasificador", "incidentes"])
        for (z, c), v in cruce.most_common(40):
            w.writerow([z, c, v])

    # --- Brecha de recursos ---
    brecha = [r for r in ranking if r[4] != "" and r[4] > UMBRAL_BRECHA_M]
    brecha.sort(key=lambda r: r[4], reverse=True)

    # --- Graficos ---
    graficos = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        meses = sorted(por_mes)
        fig, ax = plt.subplots(figsize=(11, 4.5), dpi=140)
        ax.bar(meses, [por_mes[m] for m in meses], color="#2b6cb0")
        ax.plot(meses, [por_mes[m] for m in meses], color="#c53030",
                marker="o", lw=1.4)
        ax.set_title("CMySE 911 - Incidentes por mes (2022)")
        ax.set_ylabel("Incidentes")
        ax.grid(axis="y", alpha=0.3)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        p1 = os.path.join(DIR_INFORMES, "grafico_serie_mensual.png")
        plt.savefig(p1)
        plt.close(fig)
        graficos.append(os.path.basename(p1))

        top = por_zona.most_common(15)
        fig, ax = plt.subplots(figsize=(9, 5.5), dpi=140)
        ax.barh([z for z, _ in top][::-1], [n for _, n in top][::-1],
                color="#2f855a")
        ax.set_title("CMySE 911 - Top 15 zonas por cantidad de incidentes")
        ax.set_xlabel("Incidentes")
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()
        p2 = os.path.join(DIR_INFORMES, "grafico_top_zonas.png")
        plt.savefig(p2)
        plt.close(fig)
        graficos.append(os.path.basename(p2))
    except Exception as exc:  # noqa: BLE001
        log(f"     (graficos no generados: {type(exc).__name__}: {exc})")

    # --- Informe markdown ---
    md = os.path.join(DIR_INFORMES, "ANALISIS_operativo.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# CMySE 911 - Informe de analisis espacial\n\n")
        fh.write(f"Generado: {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        fh.write("## Parametros utilizados\n\n")
        fh.write("- CRS de calculo: EPSG:5345 (metros)\n")
        fh.write(f"- KDE: radio {KDE_RADIO_M} m, pixel {KDE_PIXEL_M} m, "
                 f"{_nota_peso()}\n")
        fh.write(f"- DBSCAN: eps {DBSCAN_EPS_M} m, min {DBSCAN_MIN_PTS} puntos\n")
        fh.write(f"- K-Means: k={KMEANS_K}\n")
        fh.write(f"- Concave hull: alpha={CONCAVE_ALPHA}\n")
        fh.write(f"- Fiabilidad de la densidad: se prioriza por densidad solo con "
                 f"{MIN_INCIDENTES_ZONA}+ incidentes y {AREA_MINIMA_KM2} km2 o mas de "
                 "area. Por debajo, el poligono es una astilla y la densidad es "
                 "numericamente inestable.\n\n")

        fh.write("## Volumen total\n\n")
        fh.write(f"- Incidentes normalizados: {len(base)}\n")
        fh.write(f"- Puntos sin agrupar (ruido DBSCAN): {RUIDO_DBSCAN} "
                 f"({100*RUIDO_DBSCAN/(len(base) or 1):.1f}%)\n")
        fh.write(f"- Clusters con poligono de zona caliente: {len(ranking)}\n")
        for k, v in sorted(por_ur.items()):
            fh.write(f"- {k}: {v}\n")

        fh.write("\n## Distribucion por clasificador\n\n")
        fh.write("| Clasificador | Incidentes | % |\n|---|---|---|\n")
        total = sum(por_clasif.values()) or 1
        for k, v in por_clasif.most_common():
            fh.write(f"| {k} | {v} | {100*v/total:.1f}% |\n")

        fh.write("\n## Top 20 zonas\n\n| Zona | Incidentes |\n|---|---|\n")
        for k, v in por_zona.most_common(20):
            fh.write(f"| {k} | {v} |\n")

        fiables = [r for r in ranking
                   if r[1] >= MIN_INCIDENTES_ZONA and (r[2] or 0) >= AREA_MINIMA_KM2]
        no_fiables = [r for r in ranking if r not in fiables]
        por_densidad = sorted(fiables,
                              key=lambda r: r[3] if isinstance(r[3], (int, float)) else 0,
                              reverse=True)

        fh.write("\n## Zonas calientes priorizadas por DENSIDAD\n\n")
        fh.write("El color y el orden del mapa se definen por **densidad** "
                 "(incidentes por km2), que es lo que indica donde intervenir de "
                 "forma focalizada. El **volumen** indica carga y se conserva como "
                 "etiqueta: son dos preguntas distintas, en dos canales distintos.\n\n")
        fh.write("| Cluster | Incidentes | Area km2 | Densidad inc/km2 | "
                 "Dist. media a comisaria (m) | Zona predominante |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for r in por_densidad:
            fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[7]} |\n")

        fh.write(f"\n### Zonas excluidas de la priorizacion por densidad "
                 f"({len(no_fiables)})\n\n")
        fh.write("Se conservan en la capa y en el informe, pero no se colorean por "
                 "densidad porque su area es una astilla y el valor no es confiable.\n\n")
        fh.write("| Cluster | Incidentes | Area km2 | Densidad (no fiable) |\n")
        fh.write("|---|---|---|---|\n")
        for r in sorted(no_fiables, key=lambda x: x[1], reverse=True):
            fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |\n")

        fh.write("\n## Zonas calientes por VOLUMEN (carga)\n\n")
        fh.write("| Cluster | Incidentes | Area km2 | Densidad inc/km2 | "
                 "Dist. media a comisaria (m) | Zona predominante |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for r in ranking[:25]:
            fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[7]} |\n")

        fh.write(f"\n## Brecha de recursos: clusters a mas de "
                 f"{UMBRAL_BRECHA_M/1000:.1f} km de la comisaria mas cercana\n\n")
        if brecha:
            fh.write("| Cluster | Incidentes | Dist. media (m) | Comisaria mas cercana | Zona |\n")
            fh.write("|---|---|---|---|---|\n")
            for r in brecha:
                fh.write(f"| {r[0]} | {r[1]} | {r[4]} | {r[6]} | {r[7]} |\n")
        else:
            fh.write("_Ningun cluster supera los 2 km de distancia media a una comisaria._\n")

        fh.write("\n## Carga de demanda por dependencia policial\n\n")
        fh.write("Incidentes asignados a la dependencia mas cercana (distancia en linea recta).\n\n")
        fh.write("| Dependencia | Incidentes | Dist. media (m) | Clasificador predominante |\n")
        fh.write("|---|---|---|---|\n")
        for r in ranking_dep[:15]:
            fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |\n")

        fh.write("\n## Densidad real y estructura interna por zona (barrio)\n\n")
        fh.write("El campo `ZONA` del sistema 911 **mezcla escalas**: hay barrios de "
                 "~1,5 km2 y distritos de ~100 km2. Comparar conteos por zona sin "
                 "normalizar por superficie hace parecer \"la mas critica\" a la mas "
                 "grande, no a la mas intensa. El area se calcula por **envolvente "
                 "convexa** de los propios incidentes de la zona (mas justa que el "
                 "rectangulo envolvente, aunque tambien la sobreestima en formas "
                 "irregulares). La densidad solo se informa con "
                 f"{MIN_INCIDENTES_ZONA}+ incidentes.\n\n")
        fh.write("| Zona | Incidentes | En focos | Dispersos | Focos | Area km2 | "
                 "Densidad inc/km2 |\n")
        fh.write("|---|---|---|---|---|---|---|\n")
        for z in zonas_info[:22]:
            fh.write(f"| {z['zona']} | {z['n']} | {z['en_focos']} | "
                     f"{z['dispersos']} | {z['n_focos']} | {z['area_km2']} | "
                     f"{z['densidad_km2']} |\n")

        multi = [z for z in zonas_info if z["n_focos"] >= 2]
        if multi:
            fh.write(f"\n### Zonas con mas de un foco denso ({len(multi)})\n\n")
            fh.write("Dos o mas concentraciones separadas dentro de la MISMA etiqueta "
                     "de zona: la zona administrativa no alcanza para identificarlas, "
                     "y por eso conviene nombrarlas por cluster.\n\n")
            fh.write("| Zona | Focos | Incidentes en focos | Sep. minima (m) | "
                     "Sep. maxima (m) |\n")
            fh.write("|---|---|---|---|---|\n")
            for z in sorted(multi, key=lambda x: -x["n_focos"]):
                fh.write(f"| {z['zona']} | {z['n_focos']} | {z['en_focos']} | "
                         f"{z['sep_min_m']} | {z['sep_max_m']} |\n")

        fh.write("\n## Serie mensual\n\n| Mes | Incidentes |\n|---|---|\n")
        for m in sorted(por_mes):
            fh.write(f"| {m} | {por_mes[m]} |\n")

        fh.write("\n## Trazabilidad de pasos de Processing\n\n")
        fh.write("| Paso | Algoritmo | Estado |\n|---|---|---|\n")
        for p in pasos:
            fh.write(f"| {p['paso']} | `{p['algoritmo']}` | {p['estado']} |\n")

        if graficos:
            fh.write("\n## Graficos\n\n")
            for g in graficos:
                fh.write(f"![{g}]({g})\n\n")

        fallos = [p for p in pasos if p["estado"] == "FALLO"]
        if fallos:
            fh.write("\n## Fallos a revisar\n\n")
            for p in fallos:
                fh.write(f"- **{p['paso']}** (`{p['algoritmo']}`): {p['salida']}\n")

    log(f"     informe: {md}")
    return ranking, por_zona, por_mes, len(base)


def main():
    log("=" * 72)
    log("ANALISIS ESPACIAL CMySE 911")
    log("=" * 72)

    if not os.path.exists(MAESTRO):
        log(f"ERROR: falta {MAESTRO}. Ejecute primero 01_normalizar_911.py")
        return 2

    os.makedirs(DIR_DERIV, exist_ok=True)
    for nombre in CAPAS_DERIVADAS + ["clusters_disolucion"]:
        ruta = ruta_deriv(nombre)
        if os.path.exists(ruta):
            ogr.GetDriverByName("GPKG").DeleteDataSource(ruta)

    paso_kde()
    paso_clustering()
    paso_agregacion()
    paso_zonas_calientes()
    paso_recursos()
    consolidar()
    ranking, por_zona, por_mes, total = paso_informes()

    log("\n" + "=" * 72)
    log("RESULTADO")
    log("=" * 72)
    log(f"GeoPackage analisis : {GPKG_AN}")
    log(f"Raster KDE          : {KDE_TIF}")
    log(f"Clusters            : {len(ranking)}")
    if ranking:
        log(f"Zona mas critica    : {ranking[0][7]} ({ranking[0][1]} incidentes)")
    ok = sum(1 for p in pasos if p["estado"] == "OK")
    log(f"Pasos Processing    : {ok}/{len(pasos)} OK")
    log("\n[8/8] Listo.")
    return 0


if __name__ == "__main__":
    codigo = 1
    try:
        codigo = main()
    finally:
        qgs.exitQgis()
    sys.exit(codigo)
