# -*- coding: utf-8 -*-
r"""
04_verificar_proyecto.py
========================
Control de calidad final: abre el proyecto QGIS generado y comprueba que todas
las capas resuelvan (rutas relativas), que tengan datos y que la plantilla de
impresion exista con sus elementos.

Se ejecuta despues del pipeline. Es la verificacion que evita entregar un
proyecto que "pesa" pero abre con capas rotas.

Uso:
    _entorno_qgis.bat 04_verificar_proyecto.py [raiz_del_proyecto]

Sin argumento verifica el proyecto del workspace. Con argumento permite validar
una copia del proyecto en otra ubicacion (por ejemplo C:\Users\{USUARIO}\CMySE911),
que es la comprobacion de que las rutas relativas siguen resolviendo.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemScaleBar,
)

RAIZ_POR_DEFECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = sys.argv[1] if len(sys.argv) > 1 else RAIZ_POR_DEFECTO
PROYECTO = os.path.join(RAIZ, "03_cartografia", "CMySE911.qgz")
CRS_ESPERADO = "EPSG:5345"

fallos: list[str] = []


def log(m):
    print(m, flush=True)


def main():
    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        log("=" * 72)
        log("VERIFICACION DEL PROYECTO QGIS")
        log("=" * 72)

        if not os.path.exists(PROYECTO):
            log(f"ERROR: no existe {PROYECTO}")
            return 2

        proyecto = QgsProject.instance()
        leido = proyecto.read(PROYECTO)
        if not leido:
            log("ERROR: QGIS no pudo leer el proyecto")
            return 3
        log(f"Proyecto leido: {PROYECTO}")
        log(f"CRS del proyecto: {proyecto.crs().authid()}")
        if proyecto.crs().authid() != CRS_ESPERADO:
            fallos.append(f"CRS del proyecto {proyecto.crs().authid()} != {CRS_ESPERADO}")

        # --- Capas ---
        capas = list(proyecto.mapLayers().values())
        log(f"\nCapas en el proyecto: {len(capas)}")
        log(f"{'CAPA':<34} {'TIPO':<8} {'VALIDA':<7} {'ELEMENTOS':>10}  CRS")
        log("-" * 78)
        for capa in capas:
            nombre = capa.name()[:33]
            valida = "SI" if capa.isValid() else "NO"
            if not capa.isValid():
                fallos.append(f"capa invalida: {capa.name()} -> {capa.source()}")
            if isinstance(capa, QgsVectorLayer):
                tipo, n = "vector", capa.featureCount()
                if n <= 0:
                    fallos.append(f"capa vectorial sin entidades: {capa.name()}")
            elif isinstance(capa, QgsRasterLayer):
                tipo, n = "raster", capa.bandCount()
                if capa.width() <= 0 or capa.height() <= 0:
                    fallos.append(f"raster sin dimensiones: {capa.name()}")
            else:
                tipo, n = "otro", 0
            log(f"{nombre:<34} {tipo:<8} {valida:<7} {n:>10}  {capa.crs().authid()}")

        # --- Plantillas de impresion ---
        layouts = proyecto.layoutManager().printLayouts()
        log(f"\nPlantillas de impresion: {len(layouts)}")
        if not layouts:
            fallos.append("no hay plantillas de impresion en el proyecto")
        for lay in layouts:
            mapas = [i for i in lay.items() if isinstance(i, QgsLayoutItemMap)]
            leyendas = [i for i in lay.items() if isinstance(i, QgsLayoutItemLegend)]
            escalas = [i for i in lay.items() if isinstance(i, QgsLayoutItemScaleBar)]
            atlas = lay.atlas()
            log(f"  '{lay.name()}': {len(lay.items())} elementos, "
                f"{len(mapas)} mapas, {len(leyendas)} leyendas, {len(escalas)} escalas, "
                f"atlas={'activo' if atlas.enabled() else 'inactivo'}"
                + (f" ({atlas.count()} features)" if atlas.enabled() else ""))
            if not mapas:
                fallos.append(f"la plantilla '{lay.name()}' no tiene mapa")
            for m in mapas:
                if m.extent().isEmpty() or m.extent().width() <= 0:
                    fallos.append(f"mapa sin extension valida en '{lay.name()}'")

        # --- Entregables de cartografia ---
        log("\nArchivos de cartografia:")
        for rel in ("vista_previa.png", "atlas_zonas_calientes.pdf"):
            ruta = os.path.join(RAIZ, "03_cartografia", rel)
            if os.path.exists(ruta):
                log(f"  OK    {rel}  ({os.path.getsize(ruta)/1024/1024:.2f} MB)")
            else:
                fallos.append(f"falta {rel}")
                log(f"  FALTA {rel}")

        estilos = os.path.join(RAIZ, "03_cartografia", "estilos")
        n_qml = len([f for f in os.listdir(estilos) if f.endswith(".qml")]) if os.path.isdir(estilos) else 0
        log(f"  Estilos .qml: {n_qml}")
        if n_qml == 0:
            fallos.append("no se generaron estilos .qml")

        log("\n" + "=" * 72)
        if fallos:
            log(f"RESULTADO: {len(fallos)} PROBLEMA(S)")
            for f in fallos:
                log(f"  - {f}")
            return 1
        log("RESULTADO: proyecto verificado, sin problemas.")
        return 0
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
