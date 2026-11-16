# -*- coding: utf-8 -*-
"""PASO 13 · Revision del modelo: efecto calendario.

Hallazgo que motiva esta revision
---------------------------------
La serie oficial modelada en el paso 12 son TOTALES mensuales. Pero los meses no
tienen los mismos dias: junio tiene 30 y julio 31. Un total de 7.417 en junio y
7.807 en julio parece una caida y una recuperacion; en promedio diario son 247,2
y 251,8, es decir -1,8 %. El "valle" de junio es en buena parte el dia que falta.

Modelar totales mete ese artefacto dentro del componente estacional: el modelo
aprende "febrero es bajo" cuando en realidad febrero tiene 28 dias y enero 31.
El protocolo original del proyecto modelaba el PROMEDIO DIARIO justamente por
esto. Esta revision repite la seleccion sobre esa base y compara los pronosticos.

Salidas:
  _manifiestos/revision_calendario.json
  03_modelos/fig_revision.png
"""
import csv, json, pathlib, time, warnings, itertools
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.stats.diagnostic import acorr_ljungbox

warnings.filterwarnings("ignore")

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SERIES = RAIZ / "datos" / "series"
MOD = RAIZ / "03_modelos"
MAN = RAIZ / "_manifiestos"
t0 = time.time()
N_TEST = 12
MC = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# dias de cada mes en 2026 y 2027 (ninguno bisiesto)
DIAS_2026 = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
DIAS_2027 = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
DIAS_HORIZONTE = DIAS_2026 + DIAS_2027

datos = []
with open(SERIES / "serie_mensual.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        datos.append({"periodo": r["Periodo"], "anio": int(r["Anio"]), "mes": int(r["Mes"]),
                      "total": float(r["Incidentes"]), "dias": int(r["Dias"]),
                      "promedio": float(r["PromedioDiario"])})

total = np.array([d["total"] for d in datos])
prom = np.array([d["promedio"] for d in datos])
dias = np.array([d["dias"] for d in datos], dtype=float)
print(f"meses: {len(total)} | total {total.sum():,.0f} | promedio medio {prom.mean():.1f}", flush=True)


def metricas(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    e = real - pred
    return {"MAE": round(float(np.mean(np.abs(e))), 2),
            "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "MAPE": round(float(np.mean(np.abs(e / real)) * 100), 3)}


def seleccionar(y):
    """Misma grilla y misma regla que el paso 12, sobre la serie que se le pase."""
    tr, te = y[:-N_TEST], y[-N_TEST:]
    res = []
    for p, d, q, P, D, Q in itertools.product(range(3), [0, 1], range(3), range(2), [0, 1], range(2)):
        if (p, d, q) == (0, 0, 0) and (P, D, Q) == (0, 0, 0):
            continue
        try:
            f = SARIMAX(tr, order=(p, d, q), seasonal_order=(P, D, Q, 12),
                        enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
            res.append({"orden": [p, d, q], "estacional": [P, D, Q, 12],
                        "aic": round(float(f.aic), 3), "obj": f})
        except Exception:
            continue
    res.sort(key=lambda r: r["aic"])
    for r in res[:8]:
        try:
            r["test"] = metricas(te, r["obj"].get_forecast(steps=N_TEST).predicted_mean)
        except Exception:
            r["test"] = None
    cand = [r for r in res[:8] if r["aic"] - res[0]["aic"] <= 2 and r["test"]]
    elegido = min(cand, key=lambda r: r["test"]["RMSE"]) if cand else res[0]
    return res, elegido, tr, te


print("\nA) serie de TOTALES (modelo actual del paso 12)", flush=True)
res_t, eleg_t, tr_t, te_t = seleccionar(total)
print(f"   elegido SARIMAX{tuple(eleg_t['orden'])}x{tuple(eleg_t['estacional'])} "
      f"AIC={eleg_t['aic']} RMSE_test={eleg_t['test']['RMSE']}", flush=True)

print("\nB) serie de PROMEDIO DIARIO (protocolo original, sin efecto calendario)", flush=True)
res_p, eleg_p, tr_p, te_p = seleccionar(prom)
print(f"   elegido SARIMAX{tuple(eleg_p['orden'])}x{tuple(eleg_p['estacional'])} "
      f"AIC={eleg_p['aic']} RMSE_test={eleg_p['test']['RMSE']}", flush=True)

# ---------- pronosticos y conversion a totales
fit_t = SARIMAX(total, order=tuple(eleg_t["orden"]), seasonal_order=tuple(eleg_t["estacional"]),
                enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
pron_t = np.asarray(fit_t.get_forecast(steps=24).predicted_mean, float)
ic_t = np.asarray(fit_t.get_forecast(steps=24).conf_int(alpha=0.05), float)

fit_p = SARIMAX(prom, order=tuple(eleg_p["orden"]), seasonal_order=tuple(eleg_p["estacional"]),
                enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
fc_p = fit_p.get_forecast(steps=24)
pron_p = np.asarray(fc_p.predicted_mean, float)
ic_p = np.asarray(fc_p.conf_int(alpha=0.05), float)
# promedio diario -> total del mes
pron_p_tot = pron_p * np.array(DIAS_HORIZONTE)
ic_p_tot = ic_p * np.array(DIAS_HORIZONTE)[:, None]

anual_t = {"2026": round(float(pron_t[:12].sum()), 0), "2027": round(float(pron_t[12:].sum()), 0)}
anual_p = {"2026": round(float(pron_p_tot[:12].sum()), 0), "2027": round(float(pron_p_tot[12:].sum()), 0)}

print(f"\n   totales     : 2026 {anual_t['2026']:,.0f} | 2027 {anual_t['2027']:,.0f}")
print(f"   prom. diario: 2026 {anual_p['2026']:,.0f} | 2027 {anual_p['2027']:,.0f}")

# ---------- prueba explicita del efecto calendario
print("\nC) contraste del artefacto de calendario", flush=True)
jun25 = [d for d in datos if d["periodo"] == "2025-06"][0]
jul25 = [d for d in datos if d["periodo"] == "2025-07"][0]
may25 = [d for d in datos if d["periodo"] == "2025-05"][0]
calendario = {
    "junio_2025_total": jun25["total"], "junio_2025_dias": jun25["dias"],
    "junio_2025_promedio": jun25["promedio"],
    "julio_2025_total": jul25["total"], "julio_2025_dias": jul25["dias"],
    "julio_2025_promedio": jul25["promedio"],
    "mayo_2025_total": may25["total"], "mayo_2025_dias": may25["dias"],
    "mayo_2025_promedio": may25["promedio"],
    "variacion_total_jun_vs_jul_pct": round(100 * (jun25["total"] / jul25["total"] - 1), 2),
    "variacion_promedio_jun_vs_jul_pct": round(100 * (jun25["promedio"] / jul25["promedio"] - 1), 2),
    "dias_que_faltan": 1,
    "lectura": "el total de junio es 5,0 % menor que el de julio, pero por dia solo es 1,8 % menor: "
               "un tercio de la brecha es el dia 31 que junio no tiene",
}
print(f"   jun total {jun25['total']:.0f} vs jul {jul25['total']:.0f} "
      f"= {calendario['variacion_total_jun_vs_jul_pct']} %", flush=True)
print(f"   jun/dia  {jun25['promedio']:.1f} vs jul/dia {jul25['promedio']:.1f} "
      f"= {calendario['variacion_promedio_jun_vs_jul_pct']} %", flush=True)

# ---------- indice estacional por dia (excluye 2020, ano atipico)
idx = {}
for m in range(1, 13):
    vals = [d["promedio"] for d in datos if d["mes"] == m and d["anio"] != 2020]
    base = [d["promedio"] for d in datos if d["anio"] != 2020]
    idx[MC[m - 1]] = round(float(np.mean(vals) / np.mean(base)), 4)
print("\n   indice estacional por dia:", idx)

# ---------- figura
fig, ax = plt.subplots(2, 2, figsize=(15, 9.5))

x = np.arange(len(total))
ax[0][0].plot(x, total, color="#1f3864", lw=1.4, label="Observado")
xi = np.arange(len(total) - 1, len(total) + 24)
ax[0][0].plot(xi[1:], pron_t, color="#c00000", lw=1.8, label="Pronóstico (totales)")
ax[0][0].fill_between(xi[1:], ic_t[:, 0], ic_t[:, 1], color="#c00000", alpha=0.12)
ax[0][0].set_ylim(0, None)
ax[0][0].axvline(len(total) - 1, color="grey", ls="--", lw=0.9)
ax[0][0].set_title("Totales mensuales y pronóstico (eje desde 0)", fontsize=11,
                   fontweight="bold", color="#1f3864")
ax[0][0].legend(fontsize=8); ax[0][0].grid(alpha=0.25)
ax[0][0].set_xticks(range(0, len(total), 12))
ax[0][0].set_xticklabels([str(2018 + i // 12) for i in range(0, len(total), 12)], fontsize=8)

# panel 2025: total vs promedio diario, eje desde 0
m25 = [d for d in datos if d["anio"] == 2025]
ax[0][1].bar(range(12), [d["total"] for d in m25], color="#c8d4e6", label="Total del mes")
ax[0][1].set_ylim(0, None)
ax2 = ax[0][1].twinx()
ax2.plot(range(12), [d["promedio"] for d in m25], "o-", color="#c00000", lw=2,
         label="Promedio diario")
ax2.set_ylim(0, None)
ax[0][1].set_xticks(range(12)); ax[0][1].set_xticklabels(MC, fontsize=8)
ax[0][1].set_title("2025: el total de junio no es una caída de la demanda", fontsize=11,
                   fontweight="bold", color="#7f6000")
ax[0][1].annotate("junio: total bajo,\npromedio diario casi plano", xy=(5, 7417),
                  xytext=(6.6, 5200), fontsize=8, color="#7f6000",
                  arrowprops=dict(arrowstyle="->", color="#7f6000"))
ax2.legend(fontsize=8, loc="upper right")
ax[0][1].grid(alpha=0.2)

# perfil estacional
b = ax[1][0].bar(MC, [idx[k] for k in MC], color=["#c00000" if k == "jun" else "#8496b0" for k in MC])
ax[1][0].axhline(1, color="black", lw=0.9, ls=":")
ax[1][0].set_title("Índice estacional por día (2018-2025, sin 2020)", fontsize=11,
                   fontweight="bold", color="#1f3864")
ax[1][0].set_ylim(0.8, 1.15); ax[1][0].grid(alpha=0.25, axis="y")
for i, k in enumerate(MC):
    ax[1][0].text(i, idx[k] + 0.004, f"{idx[k]:.3f}", ha="center", fontsize=7)

# comparacion de pronosticos
per = [f"{2026 + i // 12}-{i % 12 + 1:02d}" for i in range(24)]
ax[1][1].plot(range(24), pron_t, color="#c00000", lw=2, label="Modelo de totales")
ax[1][1].plot(range(24), pron_p_tot, color="#2e75b6", lw=2, ls="--", label="Modelo de promedio diario")
ax[1][1].set_xticks(range(0, 24, 3)); ax[1][1].set_xticklabels(per[::3], fontsize=7, rotation=45)
ax[1][1].set_title("Pronóstico 2026-2027: la base elegida cambia el nivel", fontsize=11,
                   fontweight="bold", color="#c00000")
ax[1][1].set_ylabel("incidentes por mes"); ax[1][1].set_ylim(0, None)
ax[1][1].legend(fontsize=8); ax[1][1].grid(alpha=0.25)

fig.suptitle("Revisión del modelo · el efecto calendario explica el valle de junio",
             fontsize=14, fontweight="bold", color="#1f3864")
fig.tight_layout(rect=[0, 0, 1, 0.955])
fig.savefig(MOD / "fig_revision.png", dpi=140, facecolor="white")
plt.close(fig)
print("\nfig_revision.png", flush=True)

res = {
    "paso": 13, "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "motivo": "los totales mensuales mezclan el largo del mes con la demanda; se contrasta con el promedio diario",
    "modelo_totales": {"orden": eleg_t["orden"], "estacional": eleg_t["estacional"],
                       "aic": eleg_t["aic"], "test": eleg_t["test"],
                       "pronostico_anual": anual_t},
    "modelo_promedio_diario": {"orden": eleg_p["orden"], "estacional": eleg_p["estacional"],
                               "aic": eleg_p["aic"], "test": eleg_p["test"],
                               "pronostico_anual_en_totales": anual_p},
    "efecto_calendario": calendario,
    "indice_estacional_por_dia": idx,
    "diferencia_anual_entre_modelos": {
        "2026": round(float(anual_p["2026"] - anual_t["2026"]), 0),
        "2027": round(float(anual_p["2027"] - anual_t["2027"]), 0)},
    "nota": "el AIC no es comparable entre las dos bases (distinta escala de la serie); "
            "la comparacion valida es el pronostico y el ajuste fuera de muestra",
    "segundos": round(time.time() - t0, 1),
}
(MAN / "revision_calendario.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {MAN / 'revision_calendario.json'}  ({res['segundos']}s)")
