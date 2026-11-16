# -*- coding: utf-8 -*-
r"""
01_normalizar_911.py  (v2 — ingesta incremental con mapeo declarativo)
=====================================================================
Reemplaza la version que tenia los nombres de columna y los archivos escritos a
mano. Ahora la estructura del origen es **configuracion**, no codigo:

  * `mapeos_esquema.json` declara, por version de estructura, el mapeo
    COLUMNA_ORIGEN -> campo_canonico, la clave de negocio y las columnas
    obligatorias. Cuando la base del CMySE 911 cambie, se agrega un bloque de
    mapeo y NO se toca este script.
  * `config_911.json` declara los LOTES (unidad de ingesta incremental). Cada
    lote tiene su mapeo, su periodo esperado y sus fuentes. Agregar 2024, 2025 o
    cualquier anio posterior es agregar un elemento a la lista.

Comportamiento de la ingesta:
  1. DETECCION DE DERIVA: compara las columnas reales del origen contra el mapeo
     y reporta faltantes (error) y no mapeadas (aviso, con sugerencia por
     similitud). No carga un lote al que le faltan columnas obligatorias.
  2. UPSERT IDEMPOTENTE por `id_incidente`: re-procesar el mismo lote no duplica
     nada; un lote que reemite datos ya cargados los ACTUALIZA.
  3. AUDITORIA: cada actualizacion registra que campo cambio y de que valor a que
     valor, en la tabla `cambios`. Un cambio estructural del origen deja de ser
     invisible.
  4. VALIDACION POR LOTE: fechas dentro del periodo declarado, coordenadas
     dentro del bbox de control, clave no nula, duplicados internos.
  5. CONTROL: tabla `lotes` con hashes de las fuentes y conteos por corrida, y
     `validaciones` con el resultado de cada control.

Tablas de control: 01_gpkg\_control_911.sqlite  (SQLite, no GeoPackage)

Uso:
    _entorno_qgis.bat 01_normalizar_911.py [--recrear] [--solo-lote ID]
                                          [--destino RUTA.gpkg]
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

# --------------------------------------------------------------------------
# RUTAS (proyecto reubicable: la raiz se calcula, no se hardcodea)
# --------------------------------------------------------------------------
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_SCRIPTS = os.path.join(RAIZ, "04_scripts")
CONFIG = os.path.join(DIR_SCRIPTS, "config_911.json")
MAPEOS = os.path.join(DIR_SCRIPTS, "mapeos_esquema.json")
DIR_GPKG = os.path.join(RAIZ, "01_gpkg")
DIR_INFORMES = os.path.join(RAIZ, "05_informes")
DIR_STAGING = os.path.join(DIR_GPKG, "_staging")
CONTROL = os.path.join(DIR_GPKG, "_control_911.sqlite")

MAESTRO_POR_DEFECTO = os.path.join(DIR_GPKG, "CMySE911_maestro.gpkg")

CRS_ORIGEN_POR_DEFECTO = 4326
CRS_DESTINO_POR_DEFECTO = 5345
BBOX_CONTROL_POR_DEFECTO = (-67.0, -25.0, -64.0, -23.0)

# Campos canonicos de la capa de incidentes. El orden define la capa.
CAMPOS_CANONICOS = [
    ("ur_origen", ogr.OFTString, 16),
    ("lote_id", ogr.OFTString, 32),
    ("id_incidente", ogr.OFTString, 32),
    ("id_origen", ogr.OFTString, 32),
    ("fecha", ogr.OFTString, 10),
    ("anio", ogr.OFTInteger, 0),
    ("mes", ogr.OFTInteger, 0),
    ("clasificador", ogr.OFTString, 120),
    ("codigo_color", ogr.OFTString, 8),
    ("nivel_gravedad", ogr.OFTString, 12),
    ("zona", ogr.OFTString, 80),
    ("cantidad_llamadas", ogr.OFTInteger, 0),
    ("latitud", ogr.OFTReal, 0),
    ("longitud", ogr.OFTReal, 0),
    ("x_posgar", ogr.OFTReal, 0),
    ("y_posgar", ogr.OFTReal, 0),
    ("punto_texto", ogr.OFTString, 40),
    ("archivo_origen", ogr.OFTString, 64),
    ("fecha_carga", ogr.OFTString, 20),
]
# Los campos que se comparan para detectar cambios reales de datos.
CAMPOS_COMPARABLES = [c for c, _, _ in CAMPOS_CANONICOS if c != "fecha_carga"]

# Campos que el KML agrega y que se ignoran a proposito en el aviso de deriva.
CAMPOS_IGNORADOS = {
    "id", "Name", "description", "timestamp", "begin", "end", "altitudeMode",
    "tessellate", "extrude", "visibility", "drawOrder", "icon", "Style",
    "OGC_FID", "fid", "geom",
}

qa = Counter()
avisos: list[str] = []


def log(m):
    print(m, flush=True)


# --------------------------------------------------------------------------
# UTILIDADES
# --------------------------------------------------------------------------
def normalizar_texto(valor) -> str:
    """Trim, colapsa espacios, quita punto final y pasa a mayusculas."""
    if valor is None:
        return ""
    s = re.sub(r"\s+", " ", str(valor)).strip().strip(" .")
    return s.upper()


def parsear_fecha(valor):
    if valor in (None, ""):
        return None
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    s = str(valor).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M:%S", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def sha256_archivo(ruta, bloque=1 << 20):
    if not os.path.exists(ruta):
        return None
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        while True:
            trozo = fh.read(bloque)
            if not trozo:
                break
            h.update(trozo)
    return h.hexdigest()


def trafo(origen_epsg, destino_epsg):
    src = osr.SpatialReference()
    src.ImportFromEPSG(int(origen_epsg))
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(int(destino_epsg))
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


def srs_destino(epsg):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg))
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def extraer_puntos(geom):
    if geom is None or geom.IsEmpty():
        return []
    gtype = ogr.GT_Flatten(geom.GetGeometryType())
    if gtype == ogr.wkbPoint:
        return [(geom.GetX(), geom.GetY())]
    if gtype == ogr.wkbMultiPoint:
        return [(geom.GetGeometryRef(i).GetX(), geom.GetGeometryRef(i).GetY())
                for i in range(geom.GetGeometryCount())
                if geom.GetGeometryRef(i) is not None
                and not geom.GetGeometryRef(i).IsEmpty()]
    return []


def partes_poligonales(geom):
    if geom is None or geom.IsEmpty():
        return None
    gtype = ogr.GT_Flatten(geom.GetGeometryType())
    if gtype == ogr.wkbPolygon:
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        multi.AddGeometry(geom.Clone())
        return multi
    if gtype == ogr.wkbMultiPolygon:
        return geom.Clone()
    if gtype in (ogr.wkbGeometryCollection, ogr.wkbMultiSurface):
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        for i in range(geom.GetGeometryCount()):
            sub = partes_poligonales(geom.GetGeometryRef(i))
            if sub is not None:
                if ogr.GT_Flatten(sub.GetGeometryType()) == ogr.wkbPolygon:
                    multi.AddGeometry(sub)
                else:
                    for j in range(sub.GetGeometryCount()):
                        multi.AddGeometry(sub.GetGeometryRef(j))
        return multi if multi.GetGeometryCount() else None
    return None


def aplanar(geom):
    if geom is None:
        return None
    g = geom.Clone()
    try:
        g.FlattenTo2D()
    except AttributeError:
        pass
    return g


def staging_kml(ruta: str) -> str:
    os.makedirs(DIR_STAGING, exist_ok=True)
    base = re.sub(r"[^0-9A-Za-z]+", "_",
                  os.path.splitext(os.path.basename(ruta))[0]).strip("_")
    destino = os.path.join(DIR_STAGING, base + ".gpkg")
    if os.path.exists(destino):
        gdal.Unlink(destino)
    gdal.VectorTranslate(
        destino, ruta,
        options=["-f", "GPKG", "-dim", "2", "-nlt", "GEOMETRY",
                 "-lco", "GEOMETRY_NAME=geom", "-lco", "SPATIAL_INDEX=YES"])
    return destino


# --------------------------------------------------------------------------
# TABLAS DE CONTROL
# --------------------------------------------------------------------------
def abrir_control():
    os.makedirs(os.path.dirname(CONTROL), exist_ok=True)
    con = sqlite3.connect(CONTROL)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS lotes (
        lote_id TEXT PRIMARY KEY, descripcion TEXT, mapeo TEXT,
        hash_fuentes TEXT, filas_leidas INTEGER, filas_nuevas INTEGER,
        filas_actualizadas INTEGER, filas_sin_cambio INTEGER,
        filas_descartadas INTEGER, fecha_proceso TEXT, estado TEXT, detalle TEXT);
    CREATE TABLE IF NOT EXISTS cambios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id TEXT, id_incidente TEXT,
        campo TEXT, valor_anterior TEXT, valor_nuevo TEXT, fecha TEXT);
    CREATE TABLE IF NOT EXISTS validaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id TEXT, control TEXT,
        resultado TEXT, detalle TEXT, fecha TEXT);
    """)
    con.commit()
    return con


def registrar_validacion(con, lote_id, control, resultado, detalle=""):
    con.execute("INSERT INTO validaciones (lote_id, control, resultado, detalle, fecha)"
                " VALUES (?,?,?,?,?)",
                (lote_id, control, resultado, detalle,
                 dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()


# --------------------------------------------------------------------------
# DETECCION DE DERIVA DE ESTRUCTURA
# --------------------------------------------------------------------------
def sugerir(nombre, candidatos):
    """Sugerencia simple por parecido de nombre, para acelerar el mapeo nuevo."""
    base = re.sub(r"[^a-z0-9]", "", nombre.lower())
    mejores = []
    for c in candidatos:
        cb = re.sub(r"[^a-z0-9]", "", c.lower())
        if base and (base in cb or cb in base):
            mejores.append(c)
    return sorted(mejores)[:3]


def detectar_deriva(campos_origen, mapeo, etiqueta):
    """Devuelve (faltantes, sin_mapear). Reporta, no adivina."""
    declaradas = mapeo.get("columnas", {})
    faltantes = [c for c in declaradas if c not in campos_origen]
    sin_mapear = [c for c in campos_origen
                  if c not in declaradas and c not in CAMPOS_IGNORADOS]
    if faltantes:
        log(f"    DERIVA: faltan en el origen {faltantes}")
        for c in faltantes:
            sug = sugerir(c, campos_origen)
            if sug:
                log(f"      '{c}' podria corresponder a: {sug}")
    if sin_mapear:
        log(f"    DERIVA: columnas del origen sin mapear: {sin_mapear}")
        for c in sin_mapear:
            sug = sugerir(c, list(declaradas))
            if sug:
                log(f"      '{c}' podria mapearse a: {sug}")
    return faltantes, sin_mapear


# --------------------------------------------------------------------------
# CAPA CANONICA
# --------------------------------------------------------------------------
def crear_capa_incidentes(ds, epsg_destino):
    capa = ds.CreateLayer("incidentes", srs_destino(epsg_destino), ogr.wkbPoint,
                          options=["GEOMETRY_NAME=geom", "SPATIAL_INDEX=YES"])
    for nombre, tipo, ancho in CAMPOS_CANONICOS:
        fd = ogr.FieldDefn(nombre, tipo)
        if ancho:
            fd.SetWidth(ancho)
        capa.CreateField(fd)
    return capa


def abrir_o_crear_maestro(destino, epsg_destino, recrear):
    if recrear and os.path.exists(destino):
        ogr.GetDriverByName("GPKG").DeleteDataSource(destino)
    nuevo = not os.path.exists(destino)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(destino) if nuevo \
        else ogr.Open(destino, 1)
    if ds is None:
        raise RuntimeError(f"No se pudo abrir/crear {destino}")
    capa = ds.GetLayerByName("incidentes")
    if capa is None:
        crear_capa_incidentes(ds, epsg_destino)
        log("  capa 'incidentes' creada con el esquema canonico")
    else:
        # Proteccion contra un fallo peligroso: si el GeoPackage viene de la
        # version anterior del pipeline (sin id_incidente), el upsert no puede
        # reconocer los registros existentes y los DUPLICARIA todos, en silencio.
        presentes = [capa.GetLayerDefn().GetFieldDefn(i).GetName()
                     for i in range(capa.GetLayerDefn().GetFieldCount())]
        faltan = [c for c, _, _ in CAMPOS_CANONICOS if c not in presentes]
        if faltan:
            log("")
            log("ERROR: la capa 'incidentes' existente NO tiene el esquema canonico.")
            log(f"       Faltan: {faltan}")
            log("       Es una capa generada por la version anterior del pipeline.")
            log("       Ejecute una vez con --recrear para migrarla:")
            log("         01_normalizar_911.py --recrear")
            log("       Sin eso la ingesta no puede hacer upsert y duplicaria filas.")
            raise SystemExit(3)
    return ds


def indexar_existentes(capa):
    """id_incidente -> (fid, {campo: valor}) para el upsert y la auditoria."""
    indice = {}
    defn = capa.GetLayerDefn()
    nombres = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    for f in capa:
        ident = f.GetField("id_incidente")
        if ident:
            indice[ident] = (f.GetFID(), {n: f.GetField(n) for n in nombres})
    return indice


# --------------------------------------------------------------------------
# INGESTA DE UN LOTE
# --------------------------------------------------------------------------
def clave_natural(reg, claves):
    partes = []
    for c in claves:
        v = reg.get(c)
        if isinstance(v, float):
            v = round(v, 7)
        partes.append("" if v is None else str(v))
    return "|".join(partes)


def construir_registro(f, mapeo, url, archivo, fecha_carga, gravedad, bbox):
    """Aplica el mapeo declarativo y devuelve un registro canonico."""
    reg = {}
    for origen, canonico in mapeo.get("columnas", {}).items():
        reg[canonico] = f.GetField(origen)

    lat = reg.get("latitud")
    lon = reg.get("longitud")
    if (lat is None or lon is None or float(lat) == 0 or float(lon) == 0):
        pts = extraer_puntos(f.GetGeometryRef())
        if not pts:
            return None, "sin_coordenadas"
        lon, lat = pts[0]
        reg["latitud"], reg["longitud"] = lat, lon

    if not (bbox[0] <= float(lon) <= bbox[2] and bbox[1] <= float(lat) <= bbox[3]):
        return None, "fuera_de_bbox"

    fecha = parsear_fecha(reg.get("fecha"))
    if fecha is None:
        return None, "fecha_ilegible"

    color = normalizar_texto(reg.get("codigo_color"))
    reg["codigo_color"] = color
    reg["nivel_gravedad"] = gravedad.get(color, {}).get("nivel", "")
    reg["clasificador"] = normalizar_texto(reg.get("clasificador"))
    reg["zona"] = normalizar_texto(reg.get("zona"))
    reg["fecha"] = fecha.isoformat()
    reg["anio"] = fecha.year
    reg["mes"] = fecha.month
    reg["ur_origen"] = url
    reg["archivo_origen"] = archivo
    reg["fecha_carga"] = fecha_carga
    try:
        reg["cantidad_llamadas"] = int(reg.get("cantidad_llamadas") or 1)
    except (TypeError, ValueError):
        reg["cantidad_llamadas"] = 1
    if "punto_texto" in reg and reg["punto_texto"] is not None:
        reg["punto_texto"] = str(reg["punto_texto"])[:40]
    return reg, None


def ingestar_lote(ds, capa, indice, lote, mapeo, cfg, trafo_destino, con):
    lote_id = lote["lote_id"]
    descripcion = lote.get("descripcion", "")
    gravedad = cfg.get("gravedad_por_color", {})
    bbox = tuple(cfg.get("bbox_control") or BBOX_CONTROL_POR_DEFECTO)
    claves_naturales = mapeo.get("clave_negocio") or ["latitud", "longitud", "fecha",
                                                      "clasificador"]
    fecha_carga = dt.datetime.now().isoformat(timespec="seconds")
    defn = capa.GetLayerDefn()

    log(f"\n  LOTE {lote_id} — {descripcion}")
    log(f"    mapeo: {lote.get('mapeo')} | clave natural: {claves_naturales}")

    if not mapeo.get("columnas"):
        log("    LOTE OMITIDO: el mapeo no tiene columnas declaradas "
            "(estructura pendiente de completar en mapeos_esquema.json)")
        registrar_validacion(con, lote_id, "mapeo_declarado", "ERROR",
                             "el bloque de mapeo no tiene columnas")
        return {"estado": "OMITIDO", "leidas": 0, "nuevas": 0, "actualizadas": 0,
                "sin_cambio": 0, "descartadas": 0}

    hashes = []
    stats = Counter()
    vistos = {}
    fuera_periodo = 0
    periodo = lote.get("periodo_esperado") or []
    cambios_por_campo = Counter()
    hubo_error = False

    for ruta, tabla, url in lote["fuentes"]:
        if not os.path.exists(ruta):
            log(f"    FUENTE FALTANTE: {ruta}")
            registrar_validacion(con, lote_id, f"fuente:{os.path.basename(ruta)}",
                                 "ERROR", "archivo inexistente")
            hubo_error = True
            continue
        hashes.append(f"{os.path.basename(ruta)}:{sha256_archivo(ruta)}")

        ds_in = ogr.Open(ruta, 0)
        lyr = ds_in.GetLayerByName(tabla) if ds_in else None
        if lyr is None:
            log(f"    TABLA NO ENCONTRADA: '{tabla}' en {os.path.basename(ruta)}")
            registrar_validacion(con, lote_id, f"tabla:{tabla}", "ERROR",
                                 "no existe la tabla declarada")
            hubo_error = True
            continue

        campos_origen = [lyr.GetLayerDefn().GetFieldDefn(i).GetName()
                         for i in range(lyr.GetLayerDefn().GetFieldCount())]
        faltantes, sin_mapear = detectar_deriva(campos_origen, mapeo, tabla)
        if faltantes:
            registrar_validacion(con, lote_id, f"deriva:{tabla}", "ERROR",
                                 f"faltan columnas del mapeo: {faltantes}")
            hubo_error = True
            continue
        if sin_mapear:
            registrar_validacion(con, lote_id, f"deriva:{tabla}", "AVISO",
                                 f"columnas sin mapear: {sin_mapear}")

        n_fuente = 0
        for f in lyr:
            stats["leidas"] += 1
            n_fuente += 1
            reg, motivo = construir_registro(f, mapeo, url, os.path.basename(ruta),
                                             fecha_carga, gravedad, bbox)
            if reg is None:
                stats[f"descartadas_{motivo}"] += 1
                stats["descartadas"] += 1
                continue

            if periodo and len(periodo) == 2:
                if not (periodo[0] <= reg["fecha"] <= periodo[1]):
                    fuera_periodo += 1

            clave = clave_natural(reg, claves_naturales)
            ident = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:32]
            reg["id_incidente"] = ident
            reg["lote_id"] = lote_id

            if clave in vistos:
                stats["duplicados_lote"] += 1
                continue
            vistos[clave] = ident

            x, y, _ = trafo_destino.TransformPoint(reg["longitud"], reg["latitud"])
            reg["x_posgar"], reg["y_posgar"] = x, y

            anterior = indice.get(ident)
            if anterior is None:
                f_out = ogr.Feature(defn)
                for campo, _, _ in CAMPOS_CANONICOS:
                    f_out.SetField(campo, reg.get(campo))
                pt = ogr.Geometry(ogr.wkbPoint)
                pt.AddPoint_2D(x, y)
                f_out.SetGeometry(pt)
                capa.CreateFeature(f_out)
                f_out = None
                indice[ident] = (None, dict(reg))
                stats["nuevas"] += 1
            else:
                fid, valores_previos = anterior
                distintos = [c for c in CAMPOS_COMPARABLES
                             if c in reg and valores_previos.get(c) != reg.get(c)]
                if not distintos:
                    stats["sin_cambio"] += 1
                else:
                    if fid is not None:
                        f_out = ogr.Feature(defn)
                        f_out.SetFID(fid)
                        for campo, _, _ in CAMPOS_CANONICOS:
                            f_out.SetField(campo, reg.get(campo))
                        pt = ogr.Geometry(ogr.wkbPoint)
                        pt.AddPoint_2D(x, y)
                        f_out.SetGeometry(pt)
                        capa.SetFeature(f_out)
                        f_out = None
                    indice[ident] = (fid, dict(reg))
                    stats["actualizadas"] += 1
                    for c in distintos:
                        if c in ("lote_id", "archivo_origen", "ur_origen"):
                            continue   # cambios de procedencia, no de dato
                        cambios_por_campo[c] += 1
                        con.execute(
                            "INSERT INTO cambios (lote_id, id_incidente, campo,"
                            " valor_anterior, valor_nuevo, fecha) VALUES (?,?,?,?,?,?)",
                            (lote_id, ident, c, str(valores_previos.get(c)),
                             str(reg.get(c)), fecha_carga))
        log(f"    {os.path.basename(ruta):<26} {url}  leidos={n_fuente}")
        ds_in = None

    for campo, n in cambios_por_campo.most_common():
        log(f"    cambios en '{campo}': {n}")

    # --- Validaciones del lote ---
    if fuera_periodo:
        registrar_validacion(con, lote_id, "periodo_esperado", "AVISO",
                             f"{fuera_periodo} registros fuera de {periodo}")
        log(f"    AVISO: {fuera_periodo} registros fuera del periodo declarado {periodo}")
    else:
        registrar_validacion(con, lote_id, "periodo_esperado", "OK",
                             f"todos dentro de {periodo}" if periodo else "sin periodo declarado")
    if stats["descartadas"]:
        registrar_validacion(con, lote_id, "descartes", "AVISO",
                             f"{stats['descartadas']} descartados: "
                             f"{[k for k in stats if k.startswith('descartadas_')]}")
    registrar_validacion(con, lote_id, "deriva_estructura",
                         "ERROR" if hubo_error else "OK",
                         "se detectaron faltantes" if hubo_error else "sin faltantes")

    estado = "ERROR" if hubo_error else "OK"
    con.execute(
        "INSERT INTO lotes (lote_id, descripcion, mapeo, hash_fuentes, filas_leidas,"
        " filas_nuevas, filas_actualizadas, filas_sin_cambio, filas_descartadas,"
        " fecha_proceso, estado, detalle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(lote_id) DO UPDATE SET descripcion=excluded.descripcion,"
        " mapeo=excluded.mapeo, hash_fuentes=excluded.hash_fuentes,"
        " filas_leidas=excluded.filas_leidas, filas_nuevas=excluded.filas_nuevas,"
        " filas_actualizadas=excluded.filas_actualizadas,"
        " filas_sin_cambio=excluded.filas_sin_cambio,"
        " filas_descartadas=excluded.filas_descartadas,"
        " fecha_proceso=excluded.fecha_proceso, estado=excluded.estado,"
        " detalle=excluded.detalle",
        (lote_id, descripcion, lote.get("mapeo"), ";".join(hashes),
         stats["leidas"], stats["nuevas"], stats["actualizadas"],
         stats["sin_cambio"], stats["descartadas"], fecha_carga, estado,
         f"duplicados_lote={stats['duplicados_lote']}"))
    con.commit()

    log(f"    -> leidas={stats['leidas']} nuevas={stats['nuevas']} "
        f"actualizadas={stats['actualizadas']} sin_cambio={stats['sin_cambio']} "
        f"descartadas={stats['descartadas']} duplicados_internos={stats['duplicados_lote']}")
    return {"estado": estado, **stats}


# --------------------------------------------------------------------------
# CAPAS DE REFERENCIA (KML) — se reconstruyen en cada corrida
# --------------------------------------------------------------------------
def reconstruir_capa(ds, nombre, tipo_geom, campos):
    if ds.GetLayerByName(nombre) is not None:
        ds.DeleteLayer(nombre)
    capa = ds.CreateLayer(nombre, srs_destino(CRS_DESTINO_POR_DEFECTO), tipo_geom,
                          options=["GEOMETRY_NAME=geom", "SPATIAL_INDEX=YES"])
    for campo, tipo, ancho in campos:
        fd = ogr.FieldDefn(campo, tipo)
        if ancho:
            fd.SetWidth(ancho)
        capa.CreateField(fd)
    return capa


def procesar_kml(ds, cfg):
    log("\n  Capas de referencia (KML)")
    tr = trafo(CRS_ORIGEN_POR_DEFECTO, CRS_DESTINO_POR_DEFECTO)

    capa = reconstruir_capa(ds, "jurisdicciones_ur", ogr.wkbMultiPolygon,
                            [("ur", ogr.OFTString, 16), ("sector", ogr.OFTString, 24),
                             ("archivo_origen", ogr.OFTString, 64)])
    defn = capa.GetLayerDefn()
    total = 0
    for ruta, capa_kml, url in cfg.get("jurisdicciones", []):
        if not os.path.exists(ruta):
            log(f"    FALTA {os.path.basename(ruta)}")
            continue
        ds_in, lyr = (lambda d: (d, d.GetLayerByName(capa_kml)))(ogr.Open(staging_kml(ruta), 0))
        i = 0
        for f in lyr:
            poli = partes_poligonales(aplanar(f.GetGeometryRef()))
            if poli is None:
                qa["kml_geometrias_descartadas"] += 1
                continue
            poli = aplanar(poli)
            poli.Transform(tr)
            fo = ogr.Feature(defn)
            fo.SetField("ur", url)
            i += 1
            fo.SetField("sector", f"{url}-S{i:02d}")
            fo.SetField("archivo_origen", os.path.basename(ruta))
            fo.SetGeometry(poli)
            capa.CreateFeature(fo)
            fo = None
            total += 1
        log(f"    {os.path.basename(ruta):<22} {url}  poligonos={i}")
        ds_in = None
    capa = None
    qa["jurisdicciones"] = total

    capa = reconstruir_capa(ds, "zonas_cardinales", ogr.wkbGeometryCollection,
                            [("zona", ogr.OFTString, 32),
                             ("tipo_geom", ogr.OFTString, 24)])
    defn = capa.GetLayerDefn()
    total = 0
    for ruta in cfg.get("zonas_cardinales", []):
        if not os.path.exists(ruta):
            continue
        nombre = normalizar_texto(os.path.splitext(os.path.basename(ruta))[0]
                                  .replace("Cardinal", "").strip())
        ds_in, lyr = (lambda d: (d, d.GetLayer(0)))(ogr.Open(staging_kml(ruta), 0))
        for f in lyr:
            g = aplanar(f.GetGeometryRef())
            if g is None or g.IsEmpty():
                continue
            g.Transform(tr)
            fo = ogr.Feature(defn)
            fo.SetField("zona", nombre)
            fo.SetField("tipo_geom", ogr.GeometryTypeToName(g.GetGeometryType()))
            fo.SetGeometry(g)
            capa.CreateFeature(fo)
            fo = None
            total += 1
        ds_in = None
    capa = None
    qa["cardinales"] = total

    capa = reconstruir_capa(ds, "puntos_operativos", ogr.wkbPoint,
                            [("ur", ogr.OFTString, 16),
                             ("denominacion", ogr.OFTString, 64),
                             ("latitud", ogr.OFTReal, 0), ("longitud", ogr.OFTReal, 0),
                             ("x_posgar", ogr.OFTReal, 0), ("y_posgar", ogr.OFTReal, 0)])
    defn = capa.GetLayerDefn()
    total = 0
    for ruta, capa_kml, url in cfg.get("puntos_operativos", []):
        if not os.path.exists(ruta):
            continue
        ds_in, lyr = (lambda d: (d, d.GetLayerByName(capa_kml)))(ogr.Open(staging_kml(ruta), 0))
        i = 0
        for f in lyr:
            pts = extraer_puntos(f.GetGeometryRef())
            if not pts:
                continue
            lon, lat = pts[0]
            x, y, _ = tr.TransformPoint(lon, lat)
            fo = ogr.Feature(defn)
            fo.SetField("ur", url)
            fo.SetField("denominacion",
                        normalizar_texto(f.GetField("Name")) or f"{url}-SIN-NOMBRE")
            fo.SetField("latitud", lat)
            fo.SetField("longitud", lon)
            fo.SetField("x_posgar", x)
            fo.SetField("y_posgar", y)
            pt = ogr.Geometry(ogr.wkbPoint)
            pt.AddPoint_2D(x, y)
            fo.SetGeometry(pt)
            capa.CreateFeature(fo)
            fo = None
            i += 1
            total += 1
        log(f"    {os.path.basename(ruta):<22} {url}  puntos={i}")
        ds_in = None
    capa = None
    qa["operativos"] = total


# --------------------------------------------------------------------------
# INFORMES
# --------------------------------------------------------------------------
def escribir_qa(cfg, destino, resumen_lotes):
    os.makedirs(DIR_INFORMES, exist_ok=True)
    con = sqlite3.connect(f"file:{destino}?mode=ro", uri=True)
    por_anio = con.execute("SELECT anio, COUNT(*) FROM incidentes GROUP BY anio"
                           " ORDER BY anio").fetchall()
    por_clas = con.execute("SELECT clasificador, COUNT(*) FROM incidentes"
                           " GROUP BY clasificador ORDER BY COUNT(*) DESC").fetchall()
    por_ur = con.execute("SELECT ur_origen, COUNT(*) FROM incidentes"
                         " GROUP BY ur_origen ORDER BY ur_origen").fetchall()
    por_grav = con.execute("SELECT nivel_gravedad, COUNT(*) FROM incidentes"
                           " GROUP BY nivel_gravedad ORDER BY COUNT(*) DESC").fetchall()
    rango = con.execute("SELECT MIN(fecha), MAX(fecha), COUNT(*) FROM incidentes").fetchone()
    con.close()

    con = sqlite3.connect(CONTROL)
    lotes = con.execute("SELECT lote_id, estado, filas_leidas, filas_nuevas,"
                        " filas_actualizadas, filas_sin_cambio, filas_descartadas,"
                        " fecha_proceso FROM lotes ORDER BY lote_id").fetchall()
    validaciones = con.execute("SELECT lote_id, control, resultado, detalle FROM"
                               " validaciones ORDER BY id DESC LIMIT 40").fetchall()
    cambios = con.execute("SELECT lote_id, campo, COUNT(*) FROM cambios GROUP BY"
                          " lote_id, campo ORDER BY COUNT(*) DESC").fetchall()
    con.close()

    md = os.path.join(DIR_INFORMES, "QA_normalizacion.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# QA de ingesta CMySE 911\n\n")
        fh.write(f"Generado: {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        fh.write("## Estado de la capa canonica\n\n")
        fh.write(f"- Registros: **{rango[2]}**\n- Rango de fechas: {rango[0]} a {rango[1]}\n")
        fh.write(f"- Por anio: {dict(por_anio)}\n- Por UR: {dict(por_ur)}\n")
        fh.write(f"- Por nivel de gravedad: {dict(por_grav)}\n\n")
        fh.write("## Por clasificador\n\n| Clasificador | Incidentes |\n|---|---|\n")
        for k, v in por_clas:
            fh.write(f"| {k} | {v} |\n")

        fh.write("\n## Lotes procesados\n\n")
        fh.write("| Lote | Estado | Leidas | Nuevas | Actualizadas | Sin cambio | Descartadas | Fecha |\n")
        fh.write("|---|---|---|---|---|---|---|---|\n")
        for fila in lotes:
            fh.write("| " + " | ".join(str(x) for x in fila) + " |\n")

        fh.write("\n## Cambios de datos detectados (auditoria)\n\n")
        if cambios:
            fh.write("| Lote | Campo | Registros cambiados |\n|---|---|---|\n")
            for fila in cambios:
                fh.write("| " + " | ".join(str(x) for x in fila) + " |\n")
        else:
            fh.write("_Sin cambios: ninguna actualizacion modifico valores._\n")

        fh.write("\n## Validaciones\n\n| Lote | Control | Resultado | Detalle |\n|---|---|---|---|\n")
        for fila in validaciones:
            fh.write("| " + " | ".join(str(x) for x in fila) + " |\n")

        fh.write("\n## Capas de referencia\n\n")
        fh.write(f"- Jurisdicciones: {qa.get('jurisdicciones', 0)} poligonos\n")
        fh.write(f"- Zonas cardinales: {qa.get('cardinales', 0)}\n")
        fh.write(f"- Dependencias operativas: {qa.get('operativos', 0)}\n")
    return md


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    recrear = "--recrear" in args
    solo_lote = None
    destino = MAESTRO_POR_DEFECTO
    if "--solo-lote" in args:
        solo_lote = args[args.index("--solo-lote") + 1]
    if "--destino" in args:
        destino = args[args.index("--destino") + 1]

    log("=" * 72)
    log("INGESTA INCREMENTAL CMySE 911")
    log("=" * 72)

    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    with open(MAPEOS, encoding="utf-8") as fh:
        mapeos_doc = json.load(fh)
    mapeos = mapeos_doc.get("mapeos", {})
    cfg_gravedad = mapeos_doc.get("gravedad_por_color", {})
    cfg = {**cfg, "gravedad_por_color": cfg_gravedad}

    epsg_destino = int(cfg.get("crs_destino") or CRS_DESTINO_POR_DEFECTO)
    os.makedirs(DIR_GPKG, exist_ok=True)
    os.makedirs(DIR_INFORMES, exist_ok=True)

    lotes = cfg.get("lotes")
    if not lotes:
        lotes = [{"lote_id": "lote_legacy", "descripcion": "lista 'incidentes'",
                  "mapeo": cfg.get("mapeo_por_defecto", "vg_2018_2023"),
                  "periodo_esperado": [], "fuentes": cfg.get("incidentes", [])}]

    if solo_lote:
        lotes = [l for l in lotes if l["lote_id"] == solo_lote]
        if not lotes:
            log(f"ERROR: no existe el lote '{solo_lote}'")
            return 2

    con = abrir_control()
    ds = abrir_o_crear_maestro(destino, epsg_destino, recrear)
    capa = ds.GetLayerByName("incidentes")
    log(f"\n  destino: {destino}")
    log(f"  registros existentes: {capa.GetFeatureCount()}")
    indice = indexar_existentes(capa)

    resumen = {}
    for lote in lotes:
        nombre_mapeo = lote.get("mapeo") or cfg.get("mapeo_por_defecto")
        mapeo = mapeos.get(nombre_mapeo)
        if mapeo is None:
            log(f"\n  LOTE {lote['lote_id']}: mapeo '{nombre_mapeo}' no existe en"
                " mapeos_esquema.json")
            registrar_validacion(con, lote["lote_id"], "mapeo_existente", "ERROR",
                                 f"no existe el mapeo {nombre_mapeo}")
            continue
        tr = trafo(lote.get("crs_origen", CRS_ORIGEN_POR_DEFECTO), epsg_destino)
        resumen[lote["lote_id"]] = ingestar_lote(ds, capa, indice, lote, mapeo, cfg,
                                                tr, con)

    capa = None
    procesar_kml(ds, cfg)
    total = ds.GetLayerByName("incidentes").GetFeatureCount()
    ds = None

    md = escribir_qa(cfg, destino, resumen)
    con.close()

    log("\n" + "=" * 72)
    log("RESULTADO")
    log("=" * 72)
    log(f"GeoPackage    : {destino}")
    log(f"Incidentes    : {total}")
    for lid, r in resumen.items():
        log(f"  lote {lid:<12} {r.get('estado')}  nuevas={r.get('nuevas', 0)} "
            f"actualizadas={r.get('actualizadas', 0)} sin_cambio={r.get('sin_cambio', 0)}")
    log(f"Control       : {CONTROL}")
    log(f"Informe       : {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
