# -*- coding: utf-8 -*-
"""PASO 10 · Construccion de las series de tiempo.

Insumo: datos/hechos/fact_incidente.csv (1.100.708 filas, grano incidente).
Salida: datos/series/serie_diaria.csv y serie_mensual.csv + manifiesto.

La serie diaria cubre 2018-01-01 .. 2025-12-31 sin huecos; si apareciera un dia
sin registros se completa con cero y se registra en el manifiesto, porque un
hueco silencioso falsea cualquier prueba de estacionariedad.

Nota de criterio: se modela el TOTAL mensual de incidentes, que es la misma
medida que agrega el cubo (SUM(Cantidad)). El promedio diario se publica como
columna derivada para conservar la comparabilidad con el protocolo anterior.
"""
import csv, json, pathlib, time
from collections import Counter
from datetime import date, timedelta

RAIZ = pathlib.Path(__file__).resolve().parent.parent
HECHOS = RAIZ / "datos" / "hechos"
SERIES = RAIZ / "datos" / "series"
MAN = RAIZ / "_manifiestos"
SERIES.mkdir(parents=True, exist_ok=True)
t0 = time.time()

INI, FIN = date(2018, 1, 1), date(2025, 12, 31)

# ---------- conteo diario
diario = Counter()
with open(HECHOS / "fact_incidente.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        diario[r["FechaHora"][:10]] += 1
print(f"dias con datos: {len(diario)}  ({time.time()-t0:.0f}s)", flush=True)

# ---------- calendario completo
calendario, huecos = [], []
d = INI
while d <= FIN:
    iso = d.isoformat()
    v = diario.get(iso, 0)
    if v == 0:
        huecos.append(iso)
    calendario.append((iso, v, d.year, d.month, d.weekday()))
    d += timedelta(days=1)

ruta_d = SERIES / "serie_diaria.csv"
with open(ruta_d, "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh, lineterminator="\r\n")
    w.writerow(["Fecha", "Incidentes", "Anio", "Mes", "DiaSemana"])
    for iso, v, a, m, dw in calendario:
        w.writerow([iso, v, a, m, dw])
print(f"serie_diaria: {len(calendario)} filas, huecos={len(huecos)}", flush=True)

# ---------- agregacion mensual
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
mes = {}
for iso, v, a, m, _ in calendario:
    k = (a, m)
    if k not in mes:
        mes[k] = {"total": 0, "dias": 0}
    mes[k]["total"] += v
    mes[k]["dias"] += 1

ruta_m = SERIES / "serie_mensual.csv"
with open(ruta_m, "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh, lineterminator="\r\n")
    w.writerow(["Periodo", "Anio", "Mes", "NombreMes", "Incidentes", "Dias", "PromedioDiario"])
    for (a, m), d in sorted(mes.items()):
        w.writerow([f"{a}-{m:02d}", a, m, MESES[m - 1], d["total"], d["dias"],
                    round(d["total"] / d["dias"], 3)])
print(f"serie_mensual: {len(mes)} meses", flush=True)

# ---------- resumen por anio y estacionalidad
por_anio = Counter()
for iso, v, a, m, _ in calendario:
    por_anio[a] += v

estacional = {m: 0 for m in range(1, 13)}
for (a, m), d in mes.items():
    estacional[m] += d["total"] / d["dias"]

# indice estacional (media de cada mes / media global de promedios diarios)
global_diario = sum(d["total"] for d in mes.values()) / sum(d["dias"] for d in mes.values())
indice = {MESES[m - 1]: round((estacional[m] / 8) / global_diario, 4) for m in range(1, 13)}

# perfil semanal
semana = Counter()
dias_sem = Counter()
for iso, v, a, m, dw in calendario:
    semana[dw] += v
    dias_sem[dw] += 1
NOMBRES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
perfil_sem = {NOMBRES[k]: round(semana[k] / dias_sem[k], 1) for k in range(7)}

man = {
    "paso": 10,
    "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "insumo": "datos/hechos/fact_incidente.csv",
    "serie_diaria": {"filas": len(calendario), "desde": INI.isoformat(),
                     "hasta": FIN.isoformat(), "huecos": len(huecos),
                     "lista_huecos": huecos[:20], "suma": sum(v for _, v, _, _, _ in calendario)},
    "serie_mensual": {"filas": len(mes), "suma": sum(d["total"] for d in mes.values())},
    "por_anio": dict(sorted(por_anio.items())),
    "indice_estacional": indice,
    "perfil_semanal": perfil_sem,
    "media_diaria_global": round(global_diario, 2),
    "salidas": {ruta_d.name: ruta_d.stat().st_size, ruta_m.name: ruta_m.stat().st_size},
}
(MAN / "manifiesto_serie.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\nsuma diaria = {man['serie_diaria']['suma']} (debe ser 1100708)")
print("por anio:", man["por_anio"])
print("indice estacional:", indice)
print("perfil semanal:", perfil_sem)
print(f"manifiesto -> {MAN / 'manifiesto_serie.json'}  ({time.time()-t0:.0f}s)")
