# -*- coding: utf-8 -*-
r"""
06_sensibilidad.py
==================
Barrido de sensibilidad de parametros.

Responde una pregunta concreta: el resultado "el nucleo urbano es UN solo cluster
de 4.085 incidentes" es un hallazgo del fenomeno o un artefacto de haber elegido
eps = 250 m? DBSCAN encadena puntos densos: con un radio grande, zonas densas
separadas se fusionan en una sola mancha.

Metodo:
  * corre DBSCAN sobre una grilla de eps x minPts;
  * para cada combinacion mide cuantos clusters aparecen, que porcentaje queda
    como ruido, y que tamano y proporcion tiene el cluster mayor;
  * compara el cluster mayor de cada corrida contra el de la linea base
    (eps 250 / minPts 8) con el indice de Jaccard sobre el conjunto de puntos,
    que mide si es el MISMO grupo o no;
  * corre el KDE con varios radios y mide como cambia el area del nucleo caliente.

Salidas:
  * 02_analisis\sensibilidad\*.gpkg      resultados crudos de cada corrida
  * 05_informes\sensibilidad_dbscan.csv
  * 05_informes\sensibilidad_kde.csv
  * 05_informes\SENSIBILIDAD.md          conclusiones
"""

from __future__ import annotations

import csv
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication  # noqa: E402

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

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAESTRO = os.path.join(RAIZ, "01_gpkg", "CMySE911_maestro.gpkg")
DIR_SENS = os.path.join(RAIZ, "02_analisis", "sensibilidad")
DIR_INFORMES = os.path.join(RAIZ, "05_informes")

EPS = [100, 150, 200, 250, 300]
MINPTS = [5, 8, 12]
RADIOS_KDE = [300, 500, 1000]
EPS_BASE, MIN_BASE = 250, 8
PIXEL_M = 50

pasos = []


def log(m):
    print(m, flush=True)


def ruta_capa(nombre):
    return f"{MAESTRO}|layername={nombre}"


def leer_clusters(ruta_gpkg):
    """Devuelve (mapa punto->cluster, conteo por cluster)."""
    ds = ogr.Open(ruta_gpkg, 0)
    if ds is None:
        return {}, {}
    lyr = ds.GetLayer(0)
    mapa = {}
    conteo = {}
    for f in lyr:
        c = f.GetField("cluster_db")
        clave = (round(f.GetField("latitud"), 6), round(f.GetField("longitud"), 6),
                 (f.GetField("fecha") or "")[:10], f.GetField("clasificador") or "")
        if c is None or c < 0:
            mapa[clave] = None
            continue
        mapa[clave] = int(c)
        conteo[int(c)] = conteo.get(int(c), 0) + 1
    ds = None
    return mapa, conteo


def jaccard(a, b):
    if not a or not b:
        return None
    return len(a & b) / float(len(a | b))


def barrido_dbscan():
    log("\n[1/2] Barrido DBSCAN")
    os.makedirs(DIR_SENS, exist_ok=True)
    resultados = []
    mayor_base = set()

    for eps in EPS:
        for minpts in MINPTS:
            destino = os.path.join(DIR_SENS, f"dbscan_e{eps}_m{minpts}.gpkg")
            if os.path.exists(destino):
                ogr.GetDriverByName("GPKG").DeleteDataSource(destino)
            try:
                processing.run("native:dbscanclustering", {
                    "INPUT": ruta_capa("incidentes"),
                    "MIN_SIZE": minpts,
                    "EPS": eps,
                    "FIELD_NAME": "cluster_db",
                    "SIZE_FIELD_NAME": "cluster_n",
                    "OUTPUT": destino,
                })
                estado = "OK"
            except Exception as exc:  # noqa: BLE001
                log(f"    e{eps}/m{minpts}: FALLO {type(exc).__name__}")
                resultados.append({"eps": eps, "min_pts": minpts, "estado": "FALLO"})
                continue

            mapa, conteo = leer_clusters(destino)
            asignados = sum(1 for v in mapa.values() if v is not None)
            total = len(mapa)
            ruido = total - asignados
            n_clusters = len(conteo)
            if conteo:
                mayor_id = max(conteo, key=conteo.get)
                mayor_n = conteo[mayor_id]
                mayor_set = {k for k, v in mapa.items() if v == mayor_id}
                top5 = sorted(conteo.values(), reverse=True)[:5]
            else:
                mayor_n, mayor_set, top5 = 0, set(), []

            if (eps, minpts) == (EPS_BASE, MIN_BASE):
                mayor_base = set(mayor_set)

            fila = {
                "eps": eps, "min_pts": minpts, "estado": estado,
                "clusters": n_clusters, "asignados": asignados, "ruido": ruido,
                "pct_ruido": round(100.0 * ruido / total, 2) if total else "",
                "mayor_n": mayor_n,
                "mayor_pct": round(100.0 * mayor_n / total, 2) if total else "",
                "top5": "-".join(str(x) for x in top5),
                "_mayor_set": mayor_set,
            }
            resultados.append(fila)
            log(f"    eps={eps:<4} min={minpts:<3} clusters={n_clusters:<4} "
                f"ruido={fila['pct_ruido']:>5}%  mayor={mayor_n:<5} "
                f"({fila['mayor_pct']}%)")

    # Jaccard del cluster mayor contra la linea base (necesita todos los conjuntos)
    for fila in resultados:
        if fila.get("estado") != "OK":
            continue
        fila["jaccard_mayor_vs_base"] = jaccard(fila["_mayor_set"], mayor_base)
        if fila["jaccard_mayor_vs_base"] is not None:
            fila["jaccard_mayor_vs_base"] = round(fila["jaccard_mayor_vs_base"], 3)

    ruta = os.path.join(DIR_INFORMES, "sensibilidad_dbscan.csv")
    campos = ["eps", "min_pts", "estado", "clusters", "asignados", "ruido",
              "pct_ruido", "mayor_n", "mayor_pct", "jaccard_mayor_vs_base", "top5"]
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for fila in resultados:
            w.writerow(fila)
    return resultados, ruta


def barrido_kde():
    log("\n[2/2] Barrido KDE (radio)")
    resultados = []
    for radio in RADIOS_KDE:
        destino = os.path.join(DIR_SENS, f"kde_r{radio}.tif")
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass
        try:
            processing.run("qgis:heatmapkerneldensityestimation", {
                "INPUT": ruta_capa("incidentes"),
                "RADIUS": radio,
                "PIXEL_SIZE": PIXEL_M,
                "OUTPUT": destino,
            })
        except Exception as exc:  # noqa: BLE001
            log(f"    radio {radio}: FALLO {type(exc).__name__}")
            continue

        ds = gdal.Open(destino, gdal.GA_ReadOnly)
        banda = ds.GetRasterBand(1)
        arr = banda.ReadAsArray()
        maximo = float(arr.max()) if arr is not None and arr.size else 0.0
        area_pixel = (PIXEL_M * PIXEL_M) / 1e6  # km2
        fila = {"radio_m": radio, "densidad_max": round(maximo, 4)}
        for umbral in (0.25, 0.50, 0.75):
            if maximo > 0:
                n = int((arr >= umbral * maximo).sum())
            else:
                n = 0
            fila[f"km2_sobre_{int(umbral*100)}pct"] = round(n * area_pixel, 3)
        ds = None
        resultados.append(fila)
        log(f"    radio={radio:<5} max={fila['densidad_max']:<10} "
            f"nucleo>=50%={fila['km2_sobre_50pct']} km2  "

            f">=75%={fila['km2_sobre_75pct']} km2")

    ruta = os.path.join(DIR_INFORMES, "sensibilidad_kde.csv")
    campos = ["radio_m", "densidad_max", "km2_sobre_25pct",
              "km2_sobre_50pct", "km2_sobre_75pct"]
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for fila in resultados:
            w.writerow(fila)
    return resultados, ruta


def informe(db, kde, ruta_db, ruta_kde):
    log("\n[3/3] Informe de sensibilidad")

    def buscar(eps, minpts):
        for f in db:
            if f.get("eps") == eps and f.get("min_pts") == minpts and f.get("estado") == "OK":
                return f
        return None

    base = buscar(EPS_BASE, MIN_BASE)
    ajustado = buscar(100, MIN_BASE)
    amplio = buscar(300, MIN_BASE)

    # Veredicto sobre el "nucleo urbano como un solo cluster"
    veredicto = "SIN DATOS SUFICIENTES"
    detalle = ""
    if base and ajustado and amplio:
        p_base = base["mayor_pct"]
        p_aj = ajustado["mayor_pct"]
        p_am = amplio["mayor_pct"]
        if p_aj is not None and p_base is not None and p_aj < 0.5 * p_base:
            veredicto = ("ARTEFACTO DE PARAMETRO: el nucleo monolitico depende de eps")
            detalle = (f"Con eps=100 m el cluster mayor baja a {p_aj}% del total "
                       f"(contra {p_base}% con eps=250 m) y aparecen "
                       f"{ajustado['clusters']} clusters en lugar de {base['clusters']}. "
                       "El 'unico nucleo contiguo' es consecuencia del encadenamiento "
                       "a 250 m, no una propiedad estable del fenomeno.")
        elif p_am is not None and p_base is not None and p_am > 0.8 * p_base:
            veredicto = ("HALLAZGO ROBUSTO: el nucleo mayor persiste en todo el rango")
            detalle = (f"La proporcion del cluster mayor se mantiene entre "
                       f"{min(p_aj, p_base, p_am)}% y {max(p_aj, p_base, p_am)}% "
                       "en el rango explorado.")

    md = os.path.join(DIR_INFORMES, "SENSIBILIDAD.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# Analisis de sensibilidad de parametros — CMySE 911\n\n")
        fh.write("Pregunta que responde: **el resultado de que el nucleo urbano sea "
                 "un solo cluster, es un hallazgo del fenomeno o un artefacto de los "
                 "parametros elegidos?**\n\n")
        fh.write(f"## Veredicto\n\n**{veredicto}**\n\n{detalle}\n\n")

        fh.write("## Barrido DBSCAN\n\n")
        fh.write("| eps (m) | min pts | clusters | ruido % | mayor (n) | mayor % del total "
                 "| Jaccard vs base |\n|---|---|---|---|---|---|---|\n")
        for f in sorted([x for x in db if x.get("estado") == "OK"],
                        key=lambda x: (x["eps"], x["min_pts"])):
            fh.write(f"| {f['eps']} | {f['min_pts']} | {f['clusters']} | "
                     f"{f['pct_ruido']} | {f['mayor_n']} | {f['mayor_pct']} | "
                     f"{f.get('jaccard_mayor_vs_base')} |\n")
        fh.write("\n**Como leer el Jaccard**: 1,0 significa que el cluster mayor es "
                 "exactamente el mismo conjunto de puntos que en la linea base "
                 "(eps 250 / min 8); valores bajos indican que la agrupacion cambia.\n\n")

        fh.write("## Barrido KDE\n\n")
        fh.write("| radio (m) | densidad max | nucleo >=25% (km2) | >=50% (km2) | >=75% (km2) |\n")
        fh.write("|---|---|---|---|---|\n")
        for f in kde:
            fh.write(f"| {f['radio_m']} | {f['densidad_max']} | "
                     f"{f.get('km2_sobre_25pct')} | {f.get('km2_sobre_50pct')} | "
                     f"{f.get('km2_sobre_75pct')} |\n")
        fh.write("\nEl area del nucleo crece con el radio por construccion del metodo "
                 "(mayor suavizado); lo que interesa es si el **ordenamiento** de las "
                 "zonas mas densas se mantiene.\n\n")

        fh.write("## Archivos\n\n")
        fh.write(f"- Resultados crudos: `02_analisis\\sensibilidad\\`\n")
        fh.write(f"- `{os.path.basename(ruta_db)}`\n- `{os.path.basename(ruta_kde)}`\n")

    log(f"     veredicto: {veredicto}")
    log(f"     informe: {md}")
    return veredicto


def main():
    log("=" * 72)
    log("ANALISIS DE SENSIBILIDAD - CMySE 911")
    log("=" * 72)
    if not os.path.exists(MAESTRO):
        log(f"ERROR: falta {MAESTRO}")
        return 2
    os.makedirs(DIR_SENS, exist_ok=True)
    os.makedirs(DIR_INFORMES, exist_ok=True)

    db, ruta_db = barrido_dbscan()
    kde, ruta_kde = barrido_kde()
    ok = sum(1 for f in db if f.get("estado") == "OK")
    log(f"\nCorridas DBSCAN exitosas: {ok}/{len(EPS) * len(MINPTS)}")
    informe(db, kde, ruta_db, ruta_kde)
    return 0


if __name__ == "__main__":
    codigo = 1
    try:
        codigo = main()
    finally:
        qgs.exitQgis()
    sys.exit(codigo)
