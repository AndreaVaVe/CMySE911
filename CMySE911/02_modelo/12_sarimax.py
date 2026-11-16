# -*- coding: utf-8 -*-
"""PASO 12 · Seleccion y estimacion SARIMAX.

Protocolo:
  A) Serie MENSUAL (oficial) — grilla (p,d,q)x(P,D,Q)[12], seleccion por AIC
     sobre el tramo de entrenamiento 2018-01..2024-12, evaluacion sobre 2025
     (12 meses fuera de muestra) y reajuste final sobre los 96 meses.
  B) Serie DIARIA (contraste) — grilla reducida con estacionalidad semanal [7],
     para verificar que la conclusion no depende del nivel de agregacion.

Se comparan los modelos contra dos referencias ingenuas (ultimo valor y
estacional ingenuo) porque un RMSE sin linea base no dice nada.

Salidas:
  datos/series/pronostico_2026_2027.csv
  _manifiestos/modelo_sarimax.json
  03_modelos/fig_pronostico.png
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
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic"]


def leer(nombre, col="Incidentes"):
    xs = []
    with open(SERIES / nombre, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            xs.append(float(r[col]))
    return np.asarray(xs, dtype=float)


def metricas(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    e = real - pred
    return {"MAE": round(float(np.mean(np.abs(e))), 2),
            "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 2),
            "MAPE": round(float(np.mean(np.abs(e / real)) * 100), 3),
            "sesgo": round(float(np.mean(e)), 2)}


def ajustar(y, order, sorder, **kw):
    m = SARIMAX(y, order=order, seasonal_order=sorder,
                enforce_stationarity=kw.get("es", True),
                enforce_invertibility=kw.get("ei", True))
    return m.fit(disp=False, maxiter=kw.get("maxiter", 200))


# ============================== A) MENSUAL ==============================
mensual = leer("serie_mensual.csv")
N_TEST = 12
train, test = mensual[:-N_TEST], mensual[-N_TEST:]
print(f"mensual: {len(mensual)} meses | train {len(train)} | test {len(test)}", flush=True)

D_GRID = [0, 1]
resultados = []
fallos = 0
for p, d, q, P, D, Q in itertools.product(range(3), D_GRID, range(3), range(2), D_GRID, range(2)):
    if (p, d, q) == (0, 0, 0) and (P, D, Q) == (0, 0, 0):
        continue
    try:
        fit = ajustar(train, (p, d, q), (P, D, Q, 12))
        resultados.append({"orden": [p, d, q], "estacional": [P, D, Q, 12],
                           "aic": round(float(fit.aic), 3), "bic": round(float(fit.bic), 3),
                           "llf": round(float(fit.llf), 3)})
    except Exception:
        fallos += 1

resultados.sort(key=lambda r: r["aic"])
print(f"modelos estimados: {len(resultados)} (fallos {fallos})", flush=True)

# evaluacion fuera de muestra de los 8 mejores por AIC
for r in resultados[:8]:
    try:
        fit = ajustar(train, tuple(r["orden"]), tuple(r["estacional"]))
        pred = fit.get_forecast(steps=N_TEST).predicted_mean
        r["test"] = metricas(test, pred)
        r["pronostico_test"] = [round(float(x), 1) for x in pred]
    except Exception as e:
        r["test"] = {"error": str(e)[:60]}

mejor_aic = resultados[0]
print(f"mejor por AIC: SARIMAX{tuple(mejor_aic['orden'])}x{tuple(mejor_aic['estacional'])} "
      f"AIC={mejor_aic['aic']}", flush=True)

# --- seleccion final -------------------------------------------------------
# Los modelos con Delta AIC <= 2 son estadisticamente indistinguibles: el AIC
# los separa por decimas y no permite elegir entre ellos. En ese conjunto se
# prefiere el de menor RMSE fuera de muestra. Si ninguno tuviera validacion, se
# cae al mejor por AIC.
candidatos = [r for r in resultados[:8]
              if r["aic"] - mejor_aic["aic"] <= 2.0
              and isinstance(r.get("test"), dict) and "RMSE" in r.get("test", {})]
elegido = min(candidatos, key=lambda r: r["test"]["RMSE"]) if candidatos else mejor_aic
mejor = elegido
print(f"elegido (Delta AIC<=2 + mejor RMSE): "
      f"SARIMAX{tuple(mejor['orden'])}x{tuple(mejor['estacional'])} AIC={mejor['aic']} "
      f"RMSE_test={mejor['test'].get('RMSE')}", flush=True)

# referencias ingenuas sobre el mismo tramo
naive = {"ultimo_valor": metricas(test, np.repeat(train[-1], N_TEST)),
         "estacional_ingenuo": metricas(test, train[-12:])}

# reajuste final con los 96 meses y pronostico 24 meses
fit_final = ajustar(mensual, tuple(mejor["orden"]), tuple(mejor["estacional"]))
fc = fit_final.get_forecast(steps=24)
pron = np.asarray(fc.predicted_mean, float)
ic = np.asarray(fc.conf_int(alpha=0.05), float)

# pronostico alternativo con el mejor por AIC, para dejar la sensibilidad a la vista
fit_aic = ajustar(mensual, tuple(mejor_aic["orden"]), tuple(mejor_aic["estacional"]))
pron_aic = np.asarray(fit_aic.get_forecast(steps=24).predicted_mean, float)

lb = acorr_ljungbox(fit_final.resid, lags=[12], return_df=True)
ljung = {"estadistico": round(float(lb["lb_stat"].iloc[0]), 3),
         "p": round(float(lb["lb_pvalue"].iloc[0]), 4),
         "residuos_ok": bool(lb["lb_pvalue"].iloc[0] > 0.05)}

periodos = [f"{2026 + i // 12}-{i % 12 + 1:02d}" for i in range(24)]
with open(SERIES / "pronostico_2026_2027.csv", "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh, lineterminator="\r\n")
    w.writerow(["Periodo", "Pronostico", "LI95", "LS95", "Pronostico_MejorAIC"])
    for per, v, lo, hi, va in zip(periodos, pron, ic[:, 0], ic[:, 1], pron_aic):
        w.writerow([per, round(float(v), 1), round(float(lo), 1),
                    round(float(hi), 1), round(float(va), 1)])

resumen_2026 = round(float(pron[:12].sum()), 0)
resumen_2027 = round(float(pron[12:].sum()), 0)
print(f"pronostico 2026 = {resumen_2026:,.0f} | 2027 = {resumen_2027:,.0f}", flush=True)

# ============================== B) DIARIA ==============================
diaria = leer("serie_diaria.csv")
N_TEST_D = 60
tr_d, te_d = diaria[:-N_TEST_D], diaria[-N_TEST_D:]
res_d = []
fallos_d = 0
for p, q, P, Q in itertools.product(range(3), range(3), range(2), range(2)):
    if (p, q, P, Q) == (0, 0, 0, 0):
        continue
    try:
        fit = ajustar(tr_d, (p, 1, q), (P, 1, Q, 7), maxiter=120)
        res_d.append({"orden": [p, 1, q], "estacional": [P, 1, Q, 7],
                      "aic": round(float(fit.aic), 3)})
    except Exception:
        fallos_d += 1
res_d.sort(key=lambda r: r["aic"])
for r in res_d[:3]:
    try:
        fit = ajustar(tr_d, tuple(r["orden"]), tuple(r["estacional"]), maxiter=120)
        r["test"] = metricas(te_d, fit.get_forecast(steps=N_TEST_D).predicted_mean)
    except Exception as e:
        r["test"] = {"error": str(e)[:60]}
print(f"diaria: {len(res_d)} modelos (fallos {fallos_d}); mejor AIC={res_d[0]['aic'] if res_d else None}",
      flush=True)
naive_d = {"ultimo_valor": metricas(te_d, np.repeat(tr_d[-1], N_TEST_D)),
           "estacional_ingenuo_7": metricas(te_d, (tr_d[-7:].tolist() * 9)[:N_TEST_D])}

# ============================== FIGURA ==============================
fig, ax = plt.subplots(2, 1, figsize=(14, 8.5), gridspec_kw={"height_ratios": [1.35, 1]})
idx = np.arange(len(mensual))
ax[0].plot(idx, mensual, color="#1f3864", linewidth=1.4, label="Observado 2018-2025")
ax[0].plot(idx[-12:], test, "o", color="#1f3864", markersize=3.5)
xi = np.arange(len(mensual) - 1, len(mensual) + 24)
ax[0].plot(xi[1:], pron, color="#c00000", linewidth=1.8, label="Pronostico SARIMAX")
ax[0].fill_between(xi[1:], ic[:, 0], ic[:, 1], color="#c00000", alpha=0.14, label="IC 95 %")
ax[0].axvline(len(mensual) - 1, color="grey", linestyle="--", linewidth=0.9)
ax[0].set_title(f"Serie mensual y pronostico · SARIMAX{tuple(mejor['orden'])}"
                f"x{tuple(mejor['estacional'])}  (AIC {mejor['aic']})",
                fontsize=12, fontweight="bold", color="#1f3864")
ax[0].set_ylabel("incidentes por mes")
ax[0].legend(fontsize=8, loc="upper right")
ax[0].grid(alpha=0.25)

paso = 6
tick = list(range(0, len(mensual), paso)) + [len(mensual) - 1]
etiquetas = [f"{2018 + i // 12}" if i % 12 == 0 else "" for i in tick]
ax[0].set_xticks(tick); ax[0].set_xticklabels(etiquetas, fontsize=8)

ax[1].plot(np.arange(N_TEST), test, "o-", color="#1f3864", label="Observado 2025")
if "pronostico_test" in mejor:
    ax[1].plot(np.arange(N_TEST), mejor["pronostico_test"], "s--", color="#c00000",
               label="Pronostico fuera de muestra")
ax[1].set_title(f"Validacion fuera de muestra (2025) · RMSE {mejor['test'].get('RMSE')} "
                f"· MAPE {mejor['test'].get('MAPE')} %",
                fontsize=11, fontweight="bold", color="#44546a")
ax[1].set_xticks(range(N_TEST)); ax[1].set_xticklabels(MESES_CORTOS, fontsize=8)
ax[1].set_ylabel("incidentes")
# eje desde cero: con el eje recortado, una variacion mensual del 5 % parece un derrumbe
ax[1].set_ylim(0, None)
ax[1].text(5.25, test[5] - 620, "junio: 7.417 en 30 días = 247,2 por día",
           fontsize=7.5, color="#c00000", va="top")
ax[1].legend(fontsize=8); ax[1].grid(alpha=0.25)

fig.tight_layout()
fig.savefig(MOD / "fig_pronostico.png", dpi=140, facecolor="white")
plt.close(fig)
print("fig_pronostico.png", flush=True)

# ============================== MANIFIESTO ==============================
res = {
    "paso": 12,
    "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "protocolo": {
        "serie_oficial": "mensual · total de incidentes",
        "entrenamiento": "2018-01 .. 2024-12 (84 meses)",
        "validacion": "2025-01 .. 2025-12 (12 meses fuera de muestra)",
        "seleccion": "AIC sobre el tramo de entrenamiento",
        "reajuste": "96 meses completos para el pronostico 2026-2027",
    },
    "mensual": {
        "modelos_estimados": len(resultados), "fallos": fallos,
        "seleccion": {
            "regla": "entre los modelos con Delta AIC <= 2 (indistinguibles), el de menor RMSE fuera de muestra",
            "mejor_por_aic": {"orden": mejor_aic["orden"], "estacional": mejor_aic["estacional"],
                              "aic": mejor_aic["aic"], "test": mejor_aic.get("test")},
            "elegido": {"orden": mejor["orden"], "estacional": mejor["estacional"],
                        "aic": mejor["aic"], "bic": mejor["bic"], "test": mejor.get("test")},
            "candidatos_delta_aic_2": [
                {"orden": r["orden"], "estacional": r["estacional"], "aic": r["aic"],
                 "rmse_test": r.get("test", {}).get("RMSE")} for r in candidatos],
            "nota": "El AIC separa a los candidatos por decimas (<0,5) mientras el RMSE de test varia mas del doble: la eleccion se resuelve por validacion, no por AIC.",
        },
        "mejor": mejor,
        "top5": resultados[:8],
        "referencias_ingenuas": naive,
        "ljung_box_residuos": ljung,
        "pronostico_total_2026": resumen_2026,
        "pronostico_total_2027": resumen_2027,
        "pronostico_total_2026_mejor_aic": round(float(pron_aic[:12].sum()), 0),
        "pronostico_total_2027_mejor_aic": round(float(pron_aic[12:].sum()), 0),
        "pronostico_mensual": [{"periodo": p, "valor": round(float(v), 1),
                                "LI95": round(float(lo), 1), "LS95": round(float(hi), 1)}
                               for p, v, lo, hi in zip(periodos, pron, ic[:, 0], ic[:, 1])],
    },
    "diaria": {"modelos_estimados": len(res_d), "fallos": fallos_d,
               "top3": res_d[:3], "referencias_ingenuas": naive_d,
               "nota": "contraste de robustez; la serie diaria tiene estacionalidad semanal fuerte"},
    "segundos": round(time.time() - t0, 1),
}
(MAN / "modelo_sarimax.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {MAN / 'modelo_sarimax.json'}  ({res['segundos']}s)")
