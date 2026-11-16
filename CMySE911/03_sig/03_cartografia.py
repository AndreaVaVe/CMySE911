# -*- coding: utf-8 -*-
"""
03_cartografia.py
=================
Genera el proyecto QGIS con estilos profesionales y una plantilla de impresion
ejecutiva, y exporta una vista previa en PNG para verificacion.

Produce:
  - 03_cartografia\\CMySE911.qgz            proyecto listo para abrir en QGIS
  - 03_cartografia\\estilos\\*.qml          estilos reutilizables por capa
  - 03_cartografia\\vista_previa.png        verificacion automatica
  - 03_cartografia\\atlas_zonas_calientes.pdf  (Atlas por sector, si aplica)

Criterios cartograficos aplicados (para informe de gestion):
  * Composicion A4 apaisado: mapa principal + mapa de detalle + referencias.
  * Jerarquia visual: la densidad de fondo, las zonas calientes encima, y los
    recursos operativos como simbolos puntuales con etiqueta.
  * Escala grafica en km, flecha norte, grilla de coordenadas, creditos con
    fuente de datos y CRS (trazabilidad institucional).
  * Colores aptos para impresion y para daltonismo (rampa secuencial + contorno).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsCoordinateReferenceSystem, QgsPrintLayout, QgsLayoutItemMap,
    QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemScaleBar,
    QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes,
    QgsLayoutExporter, QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsSymbol, QgsSimpleFillSymbolLayer, QgsSimpleMarkerSymbolLayer,
    QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsMarkerSymbol,
    QgsGraduatedSymbolRenderer, QgsClassificationQuantile,
    QgsGradientColorRamp, QgsFillSymbol, QgsLineSymbol, QgsTextFormat,
    QgsTextBufferSettings, QgsLayoutItemMapGrid, QgsLegendStyle,
    QgsRectangle, QgsSingleSymbolRenderer, QgsRendererRange,
)
from osgeo import gdal, ogr  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

# Raiz del proyecto calculada desde la ubicacion del script (ver nota en
# 01_normalizar_911.py): el proyecto es reubicable.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAESTRO = os.path.join(RAIZ, "01_gpkg", "CMySE911_maestro.gpkg")
ANALISIS = os.path.join(RAIZ, "02_analisis", "CMySE911_analisis.gpkg")
KDE_TIF = os.path.join(RAIZ, "02_analisis", "kde_incidentes.tif")
DIR_CART = os.path.join(RAIZ, "03_cartografia")
DIR_ESTILOS = os.path.join(DIR_CART, "estilos")
PROYECTO = os.path.join(DIR_CART, "CMySE911.qgz")
VISTA = os.path.join(DIR_CART, "vista_previa.png")
ATLAS_PDF = os.path.join(DIR_CART, "atlas_zonas_calientes.pdf")

CRS = "EPSG:5345"

# Parametros de analisis. Se leen de config_911.json para que el subtitulo y el
# pie del mapa citen EXACTAMENTE los valores con los que se calculo el analisis:
# un texto fijo en el codigo se desactualiza en cuanto se ajusta eps o radio.
KDE_RADIO_M = 500
DBSCAN_EPS_M = 250
DBSCAN_MIN_PTS = 8
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_911.json")
try:
    if os.path.exists(CONFIG):
        import json
        with open(CONFIG, encoding="utf-8") as _fh:
            _cfg = json.load(_fh)
        KDE_RADIO_M = _cfg.get("kde_radio_m", KDE_RADIO_M)
        DBSCAN_EPS_M = _cfg.get("dbscan_eps_m", DBSCAN_EPS_M)
        DBSCAN_MIN_PTS = _cfg.get("dbscan_min_pts", DBSCAN_MIN_PTS)
except Exception:  # noqa: BLE001
    pass

avisos: list[str] = []


def log(m):
    print(m, flush=True)


def cargar_vector(ruta, capa, nombre):
    lyr = QgsVectorLayer(f"{ruta}|layername={capa}", nombre, "ogr")
    if not lyr.isValid():
        avisos.append(f"No se pudo cargar {nombre} desde {os.path.basename(ruta)}")
        return None
    return lyr


def col(hexstr: str, alpha: int) -> QColor:
    """Color con transparencia explicita (QColor no acepta bien #AARRGGBB)."""
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


def centro_max_densidad():
    """Coordenada del pixel de mayor densidad del KDE.

    El mapa de detalle se centra en el maximo real de densidad en lugar de en el
    centro geometrico de los datos: es una decision basada en el dato, no una
    aproximacion visual.
    """
    try:
        import numpy as np
        ds = gdal.Open(KDE_TIF, gdal.GA_ReadOnly)
        if ds is None:
            return None
        banda = ds.GetRasterBand(1).ReadAsArray()
        gt = ds.GetGeoTransform()
        fila, col = np.unravel_index(np.argmax(banda), banda.shape)
        x = gt[0] + (col + 0.5) * gt[1] + (fila + 0.5) * gt[2]
        y = gt[3] + (col + 0.5) * gt[4] + (fila + 0.5) * gt[5]
        ds = None
        return x, y
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"centro de maxima densidad: {type(exc).__name__}: {exc}")
        return None


def contar_ruido():
    """Cuenta los puntos que DBSCAN dejo sin agrupar (cluster_db nulo).

    El pie del mapa debe citar el dato real: un numero escrito a mano en el
    codigo queda desactualizado en cuanto se ajusta eps o se procesa otro lote.
    """
    try:
        ds = ogr.Open(ANALISIS, 0)
        if ds is None:
            return None
        lyr = ds.GetLayerByName("incidentes_dbscan")
        if lyr is None:
            ds = None
            return None
        n = 0
        for f in lyr:
            c = f.GetField("cluster_db")
            if c is None or c < 0:
                n += 1
        ds = None
        return n
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"conteo de ruido: {type(exc).__name__}: {exc}")
        return None


def inyectar_lienzo(ruta_qgz: str, extent) -> bool:
    """Agrega el bloque <mapcanvas> al .qgz para que QGIS abra el mapa encuadrado.

    El proyecto se genera en modo headless, es decir SIN lienzo de mapa: ese
    bloque lo escribe QGIS de escritorio al cerrar. Si falta, QGIS abre el
    proyecto con el lienzo vacio y el sintoma es "las capas cargaron pero no veo
    nada" hasta hacer Zoom a las capas. Esta funcion escribe el mismo bloque que
    produce QGIS, con el encuadre de los datos.

    No incluye <destinationsrs> a proposito: es opcional (QgsMapCanvas::readProject
    lo usa solo si esta presente), y omitirlo hace que el lienzo tome el CRS del
    proyecto, que ya es el correcto.

    Es segura: si el proyecto ya tuviera lienzo, no lo toca.
    """
    import re
    import zipfile

    if extent is None or extent.isEmpty():
        return False
    try:
        with zipfile.ZipFile(ruta_qgz, "r") as zin:
            nombres = zin.namelist()
            datos = {n: zin.read(n) for n in nombres}
    except Exception:  # noqa: BLE001
        return False

    objetivo = [n for n in nombres if n.lower().endswith(".qgs")]
    if not objetivo:
        return False
    nombre_qgs = objetivo[0]
    try:
        texto = datos[nombre_qgs].decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "<mapcanvas" in texto:
        return False
    coincidencia = re.search(r"<qgis[^>]*>", texto)
    if not coincidencia:
        return False

    bloque = (
        '\n  <mapcanvas name="theMapCanvas" annotationsVisible="1">\n'
        "    <units>meters</units>\n"
        "    <extent>\n"
        f"      <xmin>{extent.xMinimum():.3f}</xmin>\n"
        f"      <ymin>{extent.yMinimum():.3f}</ymin>\n"
        f"      <xmax>{extent.xMaximum():.3f}</xmax>\n"
        f"      <ymax>{extent.yMaximum():.3f}</ymax>\n"
        "    </extent>\n"
        "    <rotation>0</rotation>\n"
        "    <rendermaptile>0</rendermaptile>\n"
        "  </mapcanvas>\n"
    )
    corte = coincidencia.end()
    texto = texto[:corte] + bloque + texto[corte:]
    datos[nombre_qgs] = texto.encode("utf-8")

    temporal = ruta_qgz + ".tmp"
    with zipfile.ZipFile(temporal, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in nombres:
            zout.writestr(n, datos[n])
    os.replace(temporal, ruta_qgz)
    return True


def fmt(tamano: float, negrita: bool = False) -> QgsTextFormat:
    """Formato de texto moderno (setFont quedo deprecado en etiquetas de layout)."""
    t = QgsTextFormat()
    t.setFont(QFont("Arial", int(tamano), QFont.Bold if negrita else QFont.Normal))
    t.setSize(tamano)
    t.setColor(QColor("#1a202c"))
    return t


# --------------------------------------------------------------------------
# ESTILOS
# --------------------------------------------------------------------------
def estilo_kde(lyr):
    """Superficie de densidad como capa de fondo.

    Clave cartografica: en la primera parada el alfa es 0, de modo que las areas
    de densidad baja NO se pintan. Sin esto, todo el ambito de datos queda
    cubierto por el color mas frio y el mapa se vuelve ilegible.
    """
    try:
        prov = lyr.dataProvider()
        stats = prov.bandStatistics(1)
        shader = QgsColorRampShader()
        shader.setColorRampType(QgsColorRampShader.Interpolated)
        shader.setMinimumValue(0.0)
        shader.setMaximumValue(stats.maximumValue)
        vmax = stats.maximumValue or 1.0
        paradas = [
            (0.00, "#2b6cb0", 0),      # transparente: densidad despreciable
            (0.08, "#2b6cb0", 90),
            (0.25, "#38a169", 150),
            (0.45, "#d69e2e", 190),
            (0.65, "#dd6b20", 220),
            (0.85, "#c53030", 240),
            (1.00, "#742a2a", 255),
        ]
        shader.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(f * vmax, col(c, a), f"{f:.0%}")
            for f, c, a in paradas])
        rshader = QgsRasterShader()
        rshader.setRasterShaderFunction(shader)
        lyr.setRenderer(QgsSingleBandPseudoColorRenderer(prov, 1, rshader))
        lyr.setOpacity(0.9)
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo KDE: {type(exc).__name__}: {exc}")
        return False


def estilo_zonas_calientes(lyr):
    """Zonas a intervenir: clases explicitas por DENSIDAD (incidentes/km2).

    Se usan rangos FIJOS y rotulados en lenguaje operativo ("101 a 500"), no una
    clasificacion automatica: la clasificacion por cuantiles producia cortes
    incomprensibles del tipo "24,0000 - 4.085,0000" en la leyenda.
    """
    try:
        campos = [f.name() for f in lyr.fields()]
        campo = "densidad_km2" if "densidad_km2" in campos else None
        if campo:
            definiciones = [
                (0, 300, "Densidad baja (hasta 300 inc/km2)", col("#fff5eb", 150), 0.25),
                (300, 500, "Moderada (301 a 500)", col("#fdd0a2", 175), 0.35),
                (500, 800, "Alta (501 a 800)", col("#fd8d3c", 195), 0.5),
                (800, 1300, "Muy alta (801 a 1.300)", col("#d94801", 210), 0.7),
                (1300, 1e9, "Extrema (mas de 1.300)", col("#7f2704", 230), 0.9),
                # El borde crece con la densidad: los focos extremos son poligonos
                # de media hectarea y a escala de ciudad se perderian sin contorno.
            ]
            rangos = []
            for lo, hi, etiqueta, color, ancho in definiciones:
                sym = QgsFillSymbol.createSimple({
                    "color": f"{color.red()},{color.green()},{color.blue()},{color.alpha()}",
                    "outline_color": "#4a1010",
                    "outline_width": str(ancho),
                    "outline_style": "solid",
                })
                rangos.append(QgsRendererRange(float(lo), float(hi), sym, etiqueta))
            r = QgsGraduatedSymbolRenderer(campo, rangos)
        else:
            r = QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
                "color": "203,24,29,170", "outline_color": "#742a2a",
                "outline_width": "0.6",
            }))
        lyr.setRenderer(r)
        lyr.setOpacity(0.85)
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo zonas calientes: {type(exc).__name__}: {exc}")
        return False


def estilo_jurisdicciones(lyr):
    """Limites administrativos: presentes pero sin competir con el dato."""
    try:
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "0,0,0,0", "outline_color": "#4a5568",
            "outline_width": "0.35", "outline_style": "dash",
        })))
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo jurisdicciones: {type(exc).__name__}: {exc}")
        return False


def estilo_cardinales(lyr):
    try:
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "0,0,0,0", "outline_color": "#1a202c",
            "outline_width": "0.8", "outline_style": "solid",
        })))
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo cardinales: {type(exc).__name__}: {exc}")
        return False


def estilo_operativos(lyr):
    """Recursos: simbolo puntual destacado + etiqueta con la denominacion."""
    try:
        sym = QgsMarkerSymbol.createSimple({
            "name": "triangle", "color": "#1a365d",
            "outline_color": "white", "outline_width": "0.5", "size": "3.2",
        })
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))

        cfg = QgsPalLayerSettings()
        cfg.fieldName = "denominacion"
        cfg.enabled = True
        cfg.dist = 1.6
        fmt_txt = QgsTextFormat()
        fmt_txt.setFont(QFont("Arial", 5))
        fmt_txt.setSize(5)
        fmt_txt.setColor(QColor("#1a202c"))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.9)
        buf.setColor(QColor("white"))
        fmt_txt.setBuffer(buf)
        cfg.setFormat(fmt_txt)
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(cfg))
        lyr.setLabelsEnabled(True)
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo operativos: {type(exc).__name__}: {exc}")
        return False


def filtrar_y_etiquetar_zonas(lyr):
    """Muestra solo las zonas de densidad fiable y rotula su carga.

    El color dice DONDE intervenir (densidad por hectarea); la etiqueta dice
    CUANTA carga hay (cantidad de incidentes). Se excluyen del mapa las zonas de
    menos de 10 incidentes, cuyo poligono es una astilla y su densidad inestable:
    siguen en la capa y se listan aparte en el informe.
    """
    try:
        lyr.setSubsetString('"fiable_densidad" = 1')
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"filtro de zonas fiables: {type(exc).__name__}: {exc}")
    try:
        cfg = QgsPalLayerSettings()
        cfg.fieldName = "incidentes"
        cfg.enabled = True
        cfg.dist = 0.8
        t = QgsTextFormat()
        t.setFont(QFont("Arial", 8, QFont.Bold))
        t.setSize(8)
        t.setColor(QColor("#3d1a00"))
        # Tamano proporcional al volumen: los numeros grandes se leen como carga
        # alta y los chicos no tapan el mapa.
        try:
            from qgis.core import QgsProperty
            t.dataDefinedProperties().setProperty(
                QgsTextFormat.Size,
                QgsProperty.fromExpression(
                    'coalesce(scale_linear("incidentes", 10, 3000, 5.5, 10), 6)'))
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"tamano de etiqueta variable: {type(exc).__name__}")
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.9)
        buf.setColor(QColor("white"))
        t.setBuffer(buf)
        cfg.setFormat(t)
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(cfg))
        lyr.setLabelsEnabled(True)
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"etiquetas de zonas: {type(exc).__name__}: {exc}")
        return False


def estilo_incidentes(lyr):
    try:
        sym = QgsMarkerSymbol.createSimple({
            "name": "circle", "color": "#2b6cb0",
            "outline_style": "no", "size": "0.8",
        })
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        lyr.setOpacity(0.45)
        return True
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"estilo incidentes: {type(exc).__name__}: {exc}")
        return False


# --------------------------------------------------------------------------
# PLANTILLA DE IMPRESION
# --------------------------------------------------------------------------
def extent_de(layers):
    ext = None
    for l in layers:
        if l is None or not l.isValid():
            continue
        e = l.extent()
        if e.isEmpty():
            continue
        if ext is None:
            ext = e
        else:
            ext.combineExtentWith(e)
    return ext


def construir_layout(proyecto, capas):
    layout = QgsPrintLayout(proyecto)
    layout.initializeDefaults()
    layout.setName("A4_Apaisado_Ejecutivo")

    pagina = layout.pageCollection().page(0)
    pagina.setPageSize(QgsLayoutSize(297, 210, QgsUnitTypes.LayoutMillimeters))

    # --- Encabezado institucional ---
    titulo = QgsLayoutItemLabel(layout)
    titulo.setText("CMySE 911 — Mapa de calor y zonas calientes de incidentes")
    titulo.setTextFormat(fmt(16, True))
    titulo.attemptMove(QgsLayoutPoint(10, 7, QgsUnitTypes.LayoutMillimeters))
    titulo.attemptResize(QgsLayoutSize(277, 11, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(titulo)

    subtitulo = QgsLayoutItemLabel(layout)
    subtitulo.setText("San Salvador de Jujuy · Período 2022 · Análisis de densidad "
                      f"(KDE {KDE_RADIO_M} m) y agrupamiento espacial DBSCAN "
                      f"({DBSCAN_EPS_M} m / {DBSCAN_MIN_PTS} pts)")
    subtitulo.setTextFormat(fmt(9))
    subtitulo.attemptMove(QgsLayoutPoint(10, 17, QgsUnitTypes.LayoutMillimeters))
    subtitulo.attemptResize(QgsLayoutSize(277, 6, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(subtitulo)

    # --- Mapa principal ---
    mapa = QgsLayoutItemMap(layout)
    mapa.attemptMove(QgsLayoutPoint(10, 24, QgsUnitTypes.LayoutMillimeters))
    mapa.attemptResize(QgsLayoutSize(200, 148, QgsUnitTypes.LayoutMillimeters))
    mapa.setFrameEnabled(True)
    layout.addLayoutItem(mapa)

    ext = extent_de([capas.get("zonas_calientes"), capas.get("jurisdicciones_ur"),
                     capas.get("kde")])
    if ext is not None:
        ext.scale(1.06)
        mapa.setExtent(ext)

    try:
        grilla = mapa.grid()
        grilla.setEnabled(True)
        grilla.setStyle(QgsLayoutItemMapGrid.Cross)
        grilla.setIntervalX(5000)
        grilla.setIntervalY(5000)
        grilla.setAnnotationEnabled(True)
        grilla.setAnnotationTextFormat(fmt(5))
        grilla.setAnnotationPrecision(0)
        grilla.setFrameStyle(QgsLayoutItemMapGrid.LineBorder)
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"grilla: {type(exc).__name__}: {exc}")

    # --- Mapa de detalle (nucleo urbano denso) ---
    detalle = QgsLayoutItemMap(layout)
    detalle.attemptMove(QgsLayoutPoint(213, 24, QgsUnitTypes.LayoutMillimeters))
    detalle.attemptResize(QgsLayoutSize(74, 60, QgsUnitTypes.LayoutMillimeters))
    detalle.setFrameEnabled(True)
    layout.addLayoutItem(detalle)

    ext_inc = extent_de([capas.get("incidentes")])
    if ext_inc is not None:
        centro = ext_inc.center()
        d = 3500
        from qgis.core import QgsRectangle
        detalle.setExtent(QgsRectangle(centro.x() - d, centro.y() - d * 0.8,
                                       centro.x() + d, centro.y() + d * 0.8))

    etiqueta_detalle = QgsLayoutItemLabel(layout)
    etiqueta_detalle.setText("Detalle: distribución de incidentes")
    etiqueta_detalle.setTextFormat(fmt(7, True))
    etiqueta_detalle.attemptMove(QgsLayoutPoint(213, 85, QgsUnitTypes.LayoutMillimeters))
    etiqueta_detalle.attemptResize(QgsLayoutSize(74, 4, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(etiqueta_detalle)

    # --- Referencias ---
    leyenda = QgsLayoutItemLegend(layout)
    leyenda.setTitle("Referencias")
    leyenda.setLinkedMap(mapa)
    leyenda.setLegendFilterByMapEnabled(False)
    try:
        leyenda.setStyleFont(QgsLegendStyle.SymbolLabel, QFont("Arial", 6))
        leyenda.setStyleFont(QgsLegendStyle.Group, QFont("Arial", 6))
        leyenda.setStyleFont(QgsLegendStyle.Subgroup, QFont("Arial", 6))
        leyenda.setStyleFont(QgsLegendStyle.Title, QFont("Arial", 8, QFont.Bold))
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"fuente de leyenda: {type(exc).__name__}: {exc}")
    leyenda.attemptMove(QgsLayoutPoint(213, 91, QgsUnitTypes.LayoutMillimeters))
    leyenda.attemptResize(QgsLayoutSize(74, 62, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(leyenda)

    # --- Escala grafica ---
    escala = QgsLayoutItemScaleBar(layout)
    escala.setStyle("Single Box")
    escala.setLinkedMap(mapa)
    # Las unidades se fijan y DESPUES se recalcula el tamano: applyDefaultSize()
    # reinicia las unidades a metros y la barra terminaba rotulada en "m" con un
    # bloque desmedido.
    escala.setUnits(QgsUnitTypes.DistanceKilometers)
    escala.setUnitLabel("km")
    escala.setNumberOfSegments(2)
    escala.setNumberOfSegmentsLeft(0)
    escala.setUnitsPerSegment(5)
    escala.applyDefaultSize(QgsUnitTypes.DistanceKilometers)
    try:
        escala.setTextFormat(fmt(6))
    except Exception:  # noqa: BLE001
        pass
    escala.attemptMove(QgsLayoutPoint(14, 176, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(escala)

    # --- Flecha norte ---
    norte = QgsLayoutItemPicture(layout)
    norte.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    norte.attemptMove(QgsLayoutPoint(184, 172, QgsUnitTypes.LayoutMillimeters))
    norte.attemptResize(QgsLayoutSize(12, 12, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(norte)

    # --- Pie con trazabilidad ---
    ruido = contar_ruido()
    texto_ruido = (f"{ruido} puntos no agrupados (ruido). "
                   if ruido is not None else "")
    pie = QgsLayoutItemLabel(layout)
    pie.setText(
        "Fuente: CMySE 911 — 6.772 incidentes georreferenciados (año 2022). "
        "Proyección POSGAR 2007 / Argentina 3 (EPSG:5345). "
        f"Densidad por Kernel Quartic, radio {KDE_RADIO_M} m. "
        f"Clusteres por DBSCAN (eps {DBSCAN_EPS_M} m, mínimo {DBSCAN_MIN_PTS} puntos); "
        f"{texto_ruido}"
        "Zonas coloreadas por DENSIDAD (incidentes/km2); el numero indica la "
        "cantidad de incidentes. Se excluyen del mapa las zonas de menos de 10 "
        "incidentes, por densidad no fiable (ver informe). "
        "Elaborado con QGIS 3.44.11."
    )
    pie.setTextFormat(fmt(6))
    pie.attemptMove(QgsLayoutPoint(10, 190, QgsUnitTypes.LayoutMillimeters))
    pie.attemptResize(QgsLayoutSize(277, 14, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(pie)

    return layout, mapa, detalle, leyenda


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        # Bootstrap de Processing: su modulo vive en apps\qgis-ltr\python\plugins,
        # que el lanzador headless NO agrega al sys.path.
        _prefijo = os.environ.get("QGIS_PREFIX_PATH",
                                  r"C:\Program Files\QGIS 3.44.11\apps\qgis-ltr")
        for _cand in (os.path.join(_prefijo, "python", "plugins"),
                      os.path.join(_prefijo, "python")):
            if os.path.isdir(_cand) and _cand not in sys.path:
                sys.path.insert(0, _cand)
        from processing.core.Processing import Processing  # noqa: E402
        Processing.initialize()
        import processing  # noqa: E402

        os.makedirs(DIR_ESTILOS, exist_ok=True)

        log("=" * 72)
        log("CARTOGRAFIA CMySE 911")
        log("=" * 72)

        proyecto = QgsProject.instance()
        proyecto.clear()
        proyecto.setCrs(QgsCoordinateReferenceSystem(CRS))
        # Rutas relativas: el proyecto se puede mover de carpeta sin romperse
        proyecto.writeEntry("Paths", "/Absolute", False)

        capas = {}
        # Descripcion de banda: hace que la leyenda diga algo util en lugar de
        # "Band 1 (Gray)".
        try:
            ds_kde = gdal.Open(KDE_TIF, gdal.GA_Update)
            if ds_kde is not None:
                ds_kde.GetRasterBand(1).SetDescription("Densidad de incidentes (KDE)")
                ds_kde = None
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"descripcion de banda: {type(exc).__name__}")

        capas["kde"] = QgsRasterLayer(KDE_TIF, "Densidad de incidentes (KDE)", "gdal")
        if capas["kde"].isValid():
            estilo_kde(capas["kde"])
        else:
            avisos.append("no se pudo cargar el raster KDE")

        capas["zonas_calientes"] = cargar_vector(ANALISIS, "zonas_calientes",
                                                 "Zonas calientes (DBSCAN)")
        capas["jurisdicciones_ur"] = cargar_vector(MAESTRO, "jurisdicciones_ur",
                                                   "Jurisdicciones UR")
        capas["zonas_cardinales"] = cargar_vector(MAESTRO, "zonas_cardinales",
                                                  "Zonas cardinales")
        capas["puntos_operativos"] = cargar_vector(MAESTRO, "puntos_operativos",
                                                   "Dependencias policiales")
        capas["incidentes"] = cargar_vector(MAESTRO, "incidentes", "Incidentes 911")

        orden = ["kde", "zonas_calientes", "jurisdicciones_ur", "zonas_cardinales",
                 "incidentes", "puntos_operativos"]

        if capas.get("zonas_calientes"):
            estilo_zonas_calientes(capas["zonas_calientes"])
            filtrar_y_etiquetar_zonas(capas["zonas_calientes"])
        if capas.get("jurisdicciones_ur"):
            estilo_jurisdicciones(capas["jurisdicciones_ur"])
        if capas.get("zonas_cardinales"):
            estilo_cardinales(capas["zonas_cardinales"])
        if capas.get("puntos_operativos"):
            estilo_operativos(capas["puntos_operativos"])
        if capas.get("incidentes"):
            estilo_incidentes(capas["incidentes"])

        for clave in orden:
            lyr = capas.get(clave)
            if lyr is None or not lyr.isValid():
                continue
            proyecto.addMapLayer(lyr)
            # Estilo reutilizable
            try:
                ruta_estilo = os.path.join(DIR_ESTILOS, f"{clave}.qml")
                lyr.saveNamedStyle(ruta_estilo)
            except Exception as exc:  # noqa: BLE001
                avisos.append(f"estilo .qml {clave}: {type(exc).__name__}")

        layout, mapa, detalle, leyenda = construir_layout(proyecto, capas)
        proyecto.layoutManager().addLayout(layout)

        # Composicion de capas por mapa. El API correcto es setLayers(): no existe
        # setLayerVisible() en QgsLayoutItemMap. setLayers() reinicia la extension,
        # por eso se vuelve a fijar despues.
        todas = [capas[c] for c in orden if capas.get(c) and capas[c].isValid()]
        mapa.setLayers(todas)
        solo_detalle = [capas[c] for c in ("incidentes", "jurisdicciones_ur",
                                           "puntos_operativos")
                        if capas.get(c) and capas[c].isValid()]
        detalle.setLayers(solo_detalle)

        # Encuadre centrado en el DATO (no en poligonos que se extienden lejos),
        # para que el mapa no quede con grandes areas vacias.
        ext = extent_de([capas.get("incidentes"), capas.get("zonas_calientes"),
                         capas.get("puntos_operativos")])
        if ext is not None:
            ext.scale(1.06)
            mapa.setExtent(ext)

        ext_inc = extent_de([capas.get("incidentes")])
        centro_max = centro_max_densidad()
        if centro_max is not None:
            cx, cy = centro_max
            detalle.setExtent(QgsRectangle(cx - 3500, cy - 2800,
                                           cx + 3500, cy + 2800))
        elif ext_inc is not None:
            centro = ext_inc.center()
            detalle.setExtent(QgsRectangle(centro.x() - 3500, centro.y() - 2800,
                                           centro.x() + 3500, centro.y() + 2800))
        log(f"  mapa principal: {len(todas)} capas | detalle: {len(solo_detalle)} capas")

        # Limpieza de la entrada del raster en la leyenda: por defecto QGIS
        # rotula "Band 1 (Gray)" con los valores crudos. Se deja solo el nombre
        # de la capa, que es lo que un lector ejecutivo necesita.
        try:
            leyenda.setAutoUpdateModel(False)
            nodo = leyenda.model().rootGroup().findLayer(capas["kde"].id())
            if nodo is not None:
                for hijo in list(nodo.children()):
                    nodo.removeChild(hijo)
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"leyenda del raster: {type(exc).__name__}: {exc}")

        # Guardar proyecto
        proyecto.setFileName(PROYECTO)
        ok = proyecto.write()
        log(f"  proyecto guardado: {PROYECTO}  ({'OK' if ok else 'FALLO'})")

        # El proyecto headless se guarda SIN bloque <mapcanvas>: QGIS de escritorio
        # lo abre entonces con el lienzo vacio (las capas cargan, pero no se ve
        # nada hasta hacer "Zoom a las capas"). Se inyecta el encuadre de los datos
        # para que el mapa se vea apenas se abre el proyecto.
        ext_lienzo = extent_de([capas.get(c) for c in orden])
        if ext_lienzo is not None and not ext_lienzo.isEmpty():
            ext_lienzo.scale(1.02)
            if inyectar_lienzo(PROYECTO, ext_lienzo):
                log(f"  lienzo inicial inyectado "
                    f"({ext_lienzo.width()/1000:.1f} x {ext_lienzo.height()/1000:.1f} km)")
            else:
                avisos.append("no se pudo inyectar el lienzo inicial en el .qgz")

        # Vista previa
        exporter = QgsLayoutExporter(layout)
        ajustes = QgsLayoutExporter.ImageExportSettings()
        ajustes.dpi = 150
        res = exporter.exportToImage(VISTA, ajustes)
        log(f"  vista previa: {VISTA}  (resultado={res})")

        # Atlas por sector de jurisdiccion.
        # IMPORTANTE: QgsLayoutExporter.exportToPdf() con el atlas habilitado
        # producia un PDF de UNA sola pagina, aunque atlas.count() devolviera 27
        # (count() son las features de cobertura, no las paginas exportadas).
        # Se usa el algoritmo nativo native:atlaslayouttopdf, que es la ruta
        # probada de QGIS para exportar atlas y respeta todas las paginas.
        if capas.get("jurisdicciones_ur"):
            try:
                atlas = layout.atlas()
                atlas.setCoverageLayer(capas["jurisdicciones_ur"])
                atlas.setEnabled(True)
                atlas.setFilenameExpression("'sector_' || \"sector\"")
                try:
                    atlas.updateFeatures()
                except Exception:  # noqa: BLE001
                    pass
                mapa.setAtlasDriven(True)
                mapa.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
                mapa.setAtlasMargin(0.08)

                n_features = atlas.count()
                processing.run("native:atlaslayouttopdf", {
                    "LAYOUT": layout.name(),
                    "COVERAGE_LAYER": capas["jurisdicciones_ur"],
                    "OUTPUT": ATLAS_PDF,
                })
                log(f"  atlas: cobertura con {n_features} sectores -> {ATLAS_PDF}")
            except Exception as exc:  # noqa: BLE001
                avisos.append(f"atlas: {type(exc).__name__}: {exc}")

        log("\nAvisos: " + (str(len(avisos)) if avisos else "0"))
        for a in avisos:
            log(f"  - {a}")
        return 0
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
