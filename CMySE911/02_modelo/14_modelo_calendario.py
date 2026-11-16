# -*- coding: utf-8 -*-
"""PASO 14 · Modelo con correccion de calendario (especificacion definitiva).

Por que existe este paso
------------------------
El paso 13 mostro que modelar el total mensual y modelar el promedio diario
llevan a pronosticos que difieren en mas de 9.000 incidentes para 2026. Ninguna
de las dos bases es correcta por si sola:

  · el TOTAL depende del largo del mes (junio 30 dias, julio 31);
  · el PROMEDIO DIARIO depende de la composicion semanal (febrero 2025 tuvo 8
    dias de fin de semana sobre 28, enero 8 sobre 31).

La solucion estandar es modelar el total con regresores deterministicos de
calendario, que absorben ambas cosas sin tocar la dinamica de la serie:

  exog1 = dias del mes
  exog2 = dias de fin de semana (sabados + domingos) del mes

Asi el componente ARIMA queda limpio y el pronostico usa un calendario conocido.

Salidas:
  _manifiestos/modelo_calendario.json
  03_modelos/fig_calendario.png
"""
import csv, json, pathlib, time, warnings, itertools, calendar
from datetime import date, timedelta
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


def fin_de_semana(anio, mes):
    d = date(anio, mes, 1)
    n = 0
    while d.month == mes:
        if d.weekday() >= 5:
            n += 1
        d += timedelta(days=1)
    return n


datos = []
with open(SERIES / "serie_mensual.csv", encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        a, m = int(r["Anio"]), int(r["Mes"])
        datos.append({"periodo": r["Periodo"], "anio": a, "mes": m,
                      "total": float(r["Incidentes"]), "dias": int(r["Dias"]),
                      "finde": fin_de_semana(a, m)})

y = np.array([d["total"] for d in datos])
X = np.column_stack([[d["dias"] for d in datos], [d["finde"] for d in datos]]).astype(float)
print(f"meses {len(y)} | dias {X[:,0].min():.0f}-{X[:,0].max():.0f} | "
      f"fines de semana {X[:,1].min():.0f}-{X[:,1].max():.0f}", flush=True)

# calendario futuro (2026-2027), conocido de antemano
X_fut = np.array([[calendar.monthrange(a, m)[1], fin_de_semana(a, m)]
                  for a in (2026, 2027) for m in range(1, 13)], dtype=float)
periodos = [f"{2026 + i // 12}-{i % 12 + 1:02d}" for i in range(24)]


def metricas(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    e = real - pred
    return {"MAE": round(float(np.mean(np.abs(e))), 2),
            "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 2),
            "MAPE": round(float(np.mean(np.abs(e / real)) * 100), 3)}


tr_y, te_y = y[:-N_TEST], y[-N_TEST:]
tr_X, te_X = X[:-N_TEST], X[-N_TEST:]

res = []
fallos = 0
for p, d, q, P, D, Q in itertools.product(range(3), [1], range(3), range(2), [0, 1], range(2)):
    if (p, d, q) == (0, 0, 0) and (P, D, Q) == (0, 0, 0):
        continue
    try:
        f = SARIMAX(tr_y, exog=tr_X, order=(p, d, q), seasonal_order=(P, D, Q, 12),
                    enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
        res.append({"orden": [p, d, q], "estacional": [P, D, Q, 12],
                    "aic": round(float(f.aic), 3), "obj": f})
    except Exception:
        fallos += 1
res.sort(key=lambda r: r["aic"])
print(f"modelos estimados {len(res)} (fallos {fallos})", flush=True)

for r in res[:8]:
    try:
        r["test"] = metricas(te_y, r["obj"].get_forecast(steps=N_TEST, exog=te_X).predicted_mean)
    except Exception:
        r["test"] = None

cand = [r for r in res[:8] if r["aic"] - res[0]["aic"] <= 2 and r["test"]]
elegido = min(cand, key=lambda r: r["test"]["RMSE"]) if cand else res[0]
print(f"elegido SARIMAX{tuple(elegido['orden'])}x{tuple(elegido['estacional'])} "
      f"AIC={elegido['aic']} RMSE_test={elegido['test']['RMSE']} "
      f"MAPE={elegido['test']['MAPE']}", flush=True)

# reajuste con todo y pronostico con calendario conocido
fit = SARIMAX(y, exog=X, order=tuple(elegido["orden"]),
              seasonal_order=tuple(elegido["estacional"]),
              enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
fc = fit.get_forecast(steps=24, exog=X_fut)
pron = np.asarray(fc.predicted_mean, float)
ic = np.asarray(fc.conf_int(alpha=0.05), float)

lb = acorr_ljungbox(fit.resid, lags=[12], return_df=True)
ljung = {"estadistico": round(float(lb["lb_stat"].iloc[0]), 3),
         "p": round(float(lb["lb_pvalue"].iloc[0]), 4)}

anual = {"2026": round(float(pron[:12].sum()), 0), "2027": round(float(pron[12:].sum()), 0)}
print(f"\npronostico 2026 {anual['2026']:,.0f} | 2027 {anual['2027']:,.0f}", flush=True)
print(f"Ljung-Box p={ljung['p']}", flush=True)

# contraste de las tres especificaciones
prev = json.loads((MAN / "revision_calendario.json").read_text(encoding="utf-8"))
comp = {
    "totales_sin_calendario": prev["modelo_totales"]["pronostico_anual"],
    "promedio_diario": prev["modelo_promedio_diario"]["pronostico_anual_en_totales"],
    "totales_con_calendario": anual,
}
print("\ncontraste de especificaciones:", comp, flush=True)

# junio 2026 con y sin calendario
i_jun = periodos.index("2026-06")
print(f"junio 2026 pronosticado: {pron[i_jun]:,.0f} (con calendario) "
      f"| dias={X_fut[i_jun,0]:.0f} findes={X_fut[i_jun,1]:.0f}", flush=True)

# ---------- figura
fig, ax = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw={"width_ratios": [1.5, 1]})
x = np.arange(len(y))
ax[0].plot(x, y, color="#1f3864", lw=1.4, label="Observado 2018-2025")
ax[0].plot(x[-12:], te_y, "o", color="#1f3864", ms=3.5)
xi = np.arange(len(y) - 1, len(y) + 24)
ax[0].plot(xi[1:], pron, color="#2e7d32", lw=2.1, label="Pronóstico con calendario")
ax[0].fill_between(xi[1:], ic[:, 0], ic[:, 1], color="#2e7d32", alpha=0.13, label="IC 95 %")
ax[0].axvline(len(y) - 1, color="grey", ls="--", lw=0.9)
ax[0].set_ylim(0, None)
ax[0].set_title(f"Modelo con regresores de calendario · SARIMAX{tuple(elegido['orden'])}"
                f"x{tuple(elegido['estacional'])}", fontsize=11.5, fontweight="bold", color="#2e7d32")
ax[0].set_ylabel("incidentes por mes"); ax[0].legend(fontsize=8); ax[0].grid(alpha=0.25)
ax[0].set_xticks(range(0, len(y), 12))
ax[0].set_xticklabels([str(2018 + i // 12) for i in range(0, len(y), 12)], fontsize=8)

# promedio diario observado vs ajuste sin efecto calendario
ajuste_diario = (y / X[:, 0])
ax[1].plot(range(96), ajuste_diario, color="#8496b0", lw=1.0, label="Promedio diario observado")
ax[1].set_ylim(0, None)
ax[1].set_title("Por qué junio no es una caída:\nla demanda por día es suave", fontsize=11.5,
                fontweight="bold", color="#7f6000")
ax[1].set_ylabel("incidentes por día")
m25 = [d for d in datos if d["anio"] == 2025]
base = 96 - 12
ax[1].plot(range(base, 96), [d["total"] / d["dias"] for d in m25], "o-", color="#c00000",
           lw=2, label="2025")
ax[1].annotate("junio", xy=(base + 5, m25[5]["total"] / m25[5]["dias"]),
               xytext=(base + 2.2, m25[5]["total"] / m25[5]["dias"] - 45),
               fontsize=9, color="#c00000",
               arrowprops=dict(arrowstyle="->", color="#c00000"))
ax[1].set_xticks(range(0, 96, 12))
ax[1].set_xticklabels([str(2018 + i // 12) for i in range(0, 96, 12)], fontsize=8)
ax[1].legend(fontsize=8); ax[1].grid(alpha=0.25)

fig.suptitle("Especificación definitiva: el total mensual se modela con el calendario como regresor",
             fontsize=13.5, fontweight="bold", color="#1f3864")
fig.tight_layout(rect=[0, 0, 1, 0.93])
fig.savefig(MOD / "fig_calendario.png", dpi=140, facecolor="white")
plt.close(fig)

out = {
    "paso": 14, "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "especificacion": "SARIMAX sobre el total mensual con exog = [dias del mes, dias de fin de semana]",
    "por_que": "el total puro depende del largo del mes y el promedio diario de la composicion semanal; "
               "el regresor absorbe ambos efectos y deja el componente ARIMA limpio",
    "modelos_estimados": len(res), "fallos": fallos,
    "elegido": {"orden": elegido["orden"], "estacional": elegido["estacional"],
                "aic": elegido["aic"], "test": elegido["test"]},
    "top5": [{"orden": r["orden"], "estacional": r["estacional"], "aic": r["aic"],
              "rmse_test": r["test"]["RMSE"] if r.get("test") else None} for r in res[:5]],
    "ljung_box": ljung,
    "pronostico_anual": anual,
    "pronostico_mensual": [{"periodo": p, "valor": round(float(v), 1),
                            "LI95": round(float(lo), 1), "LS95": round(float(hi), 1),
                            "dias": int(dd), "findes": int(fs)}
                           for p, v, lo, hi, (dd, fs) in zip(periodos, pron, ic[:, 0], ic[:, 1], X_fut)],
    "contraste_especificaciones": comp,
    "segundos": round(time.time() - t0, 1),
}
(MAN / "modelo_calendario.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n-> {MAN / 'modelo_calendario.json'}  ({out['segundos']}s)")
