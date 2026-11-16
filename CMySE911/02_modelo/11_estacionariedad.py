# -*- coding: utf-8 -*-
"""PASO 11 · Analisis de estacionariedad (ADF + KPSS).

Se aplican las dos pruebas juntas porque tienen hipotesis nulas opuestas:
  ADF  H0 = hay raiz unitaria (no estacionaria)   -> se quiere rechazar
  KPSS H0 = la serie es estacionaria              -> se quiere NO rechazar
El diagnostico solo se declara solido cuando ambas coinciden.

Cubre la serie diaria (m=7) y la mensual (m=12), en nivel, en primera
diferencia y en diferencia estacional.
Salida: datos/series/estacionariedad.json + 03_modelos/fig_estacionariedad.png
"""
import csv, json, pathlib, time, warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statsmodels.tsa.stattools import adfuller, kpss

warnings.filterwarnings("ignore")

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SERIES = RAIZ / "datos" / "series"
MOD = RAIZ / "03_modelos"
MAN = RAIZ / "_manifiestos"
t0 = time.time()


def leer(nombre, col):
    xs = []
    with open(SERIES / nombre, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            xs.append(float(r[col]))
    return np.asarray(xs, dtype=float)


diaria = leer("serie_diaria.csv", "Incidentes")
mensual = leer("serie_mensual.csv", "Incidentes")


def probar(nombre, x, m):
    r = {"n": int(len(x)), "media": round(float(np.mean(x)), 3),
         "desvio": round(float(np.std(x, ddof=1)), 3), "varianza": round(float(np.var(x, ddof=1)), 1)}
    try:
        a = adfuller(x, autolag="AIC", regression="c")
        r["adf"] = {"estadistico": round(float(a[0]), 4), "p": round(float(a[1]), 4),
                    "lags": int(a[2]), "n": int(a[3]),
                    "rechaza_raiz_unitaria": bool(a[1] < 0.05)}
    except Exception as e:
        r["adf"] = {"error": str(e)[:80]}
    try:
        k = kpss(x, regression="c", nlags="auto")
        r["kpss"] = {"estadistico": round(float(k[0]), 4), "p": round(float(k[1]), 4),
                     "lags": int(k[2]),
                     "rechaza_estacionariedad": bool(k[1] < 0.05)}
    except Exception as e:
        r["kpss"] = {"error": str(e)[:80]}
    a_ok = r.get("adf", {}).get("rechaza_raiz_unitaria")
    k_ok = r.get("kpss", {}).get("rechaza_estacionariedad")
    if a_ok is None or k_ok is None:
        r["diagnostico"] = "no concluyente (prueba fallida)"
    elif a_ok and not k_ok:
        r["diagnostico"] = "ESTACIONARIA (ADF rechaza raiz unitaria y KPSS no rechaza)"
    elif (not a_ok) and k_ok:
        r["diagnostico"] = "NO ESTACIONARIA (ambas pruebas coinciden)"
    elif a_ok and k_ok:
        r["diagnostico"] = "AMBIGUO (ADF rechaza, KPSS tambien rechaza)"
    else:
        r["diagnostico"] = "AMBIGUA (ninguna rechaza)"
    print(f"  {nombre:26} n={len(x):5}  ADF p={r.get('adf',{}).get('p')}  "
          f"KPSS p={r.get('kpss',{}).get('p')}  -> {r['diagnostico']}", flush=True)
    return r


print("PRUEBAS DE ESTACIONARIEDAD", flush=True)
res = {}
res["diaria_nivel"] = probar("diaria · nivel", diaria, 7)
res["diaria_d1"] = probar("diaria · 1a diferencia", np.diff(diaria), 7)
res["diaria_d7"] = probar("diaria · diferencia 7 (semanal)", diaria[7:] - diaria[:-7], 7)
res["mensual_nivel"] = probar("mensual · nivel", mensual, 12)
res["mensual_d1"] = probar("mensual · 1a diferencia", np.diff(mensual), 12)
res["mensual_d12"] = probar("mensual · diferencia 12 (anual)", mensual[12:] - mensual[:-12], 12)

# ---------- orden de diferenciacion sugerido
def orden(clave_nivel, clave_d1, clave_d12):
    if res[clave_nivel]["diagnostico"].startswith("ESTACIONARIA"):
        return 0, 0
    if res[clave_d1]["diagnostico"].startswith("ESTACIONARIA"):
        return 1, 0
    if res[clave_d1]["diagnostico"].startswith("NO") and res[clave_d12]["diagnostico"].startswith("ESTACIONARIA"):
        return 1, 1
    return 1, 1


d_diaria = orden("diaria_nivel", "diaria_d1", "diaria_d7")
d_mensual = orden("mensual_nivel", "mensual_d1", "mensual_d12")
res["orden_sugerido"] = {"diaria": {"d": d_diaria[0], "D": d_diaria[1], "m": 7},
                         "mensual": {"d": d_mensual[0], "D": d_mensual[1], "m": 12}}
print(f"\norden sugerido diaria (d,D)={d_diaria}  mensual (d,D)={d_mensual}", flush=True)

# ---------- figura
fig, ax = plt.subplots(3, 2, figsize=(15, 10))
paneles = [
    ("Diaria · nivel", diaria, "#1f3864"),
    ("Diaria · 1ª diferencia", np.diff(diaria), "#1f4e79"),
    ("Diaria · diferencia 7 (semanal)", diaria[7:] - diaria[:-7], "#2e75b6"),
    ("Mensual · nivel", mensual, "#7f6000"),
    ("Mensual · 1ª diferencia", np.diff(mensual), "#bf9000"),
    ("Mensual · diferencia 12 (anual)", mensual[12:] - mensual[:-12], "#d6a300"),
]
for i, (titulo, y, c) in enumerate(paneles):
    a = ax[i % 3][i // 3]
    a.plot(y, color=c, linewidth=0.8)
    a.set_title(titulo, fontsize=10.5, fontweight="bold", color=c)
    a.grid(alpha=0.25)
    a.axhline(0, color="grey", linewidth=0.6, linestyle=":")
    a.set_ylabel("incidentes", fontsize=8)
fig.suptitle("Estacionariedad · serie reconstruida 2018-2025 (1.100.708 registros)",
             fontsize=13.5, fontweight="bold", color="#1f3864")
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig(MOD / "fig_estacionariedad.png", dpi=140, facecolor="white")
plt.close(fig)
print("fig_estacionariedad.png", flush=True)

res["generado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
res["insumo"] = {
    "diaria": {"filas": len(diaria), "suma": int(diaria.sum())},
    "mensual": {"filas": len(mensual), "suma": int(mensual.sum())},
}
res["segundos"] = round(time.time() - t0, 1)
(MAN / "estacionariedad.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {MAN / 'estacionariedad.json'}  ({res['segundos']}s)")
