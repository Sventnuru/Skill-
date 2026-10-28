# Прогнозы по дивидендным историям: запись ДО события, проверка ПОСЛЕ, оценка калибровки. Версия 1 (28.10.2026).
# Прогноз — вероятностное утверждение на базе прошлых отсечек (div_history.py), а не совет. Журнал только дописывается.
# Запуск (из папки со скриптами):
#   python div_forecast.py make    --history div_candidates.json --log predictions.csv [--status "рекомендовано СД"]
#   python div_forecast.py resolve --log predictions.csv
#   python div_forecast.py score   --log predictions.csv
import sys, csv, json, math, random
from datetime import datetime, date, timedelta
import div_history as H

MODEL = "v1-пулинг-8"
A = 8  # сила сжатия к общему: тикер с n событий весит n/(n+8)
FIELDS = ["key", "made_at", "ticker", "t1", "record", "dps", "price", "status", "model", "n_ticker",
          "gap_open_med", "gap_open_q25", "gap_open_q75", "base_gap_open_med", "p_close20", "base_p_close20",
          "p_runup_pos", "runup_window_started",
          "gap_open_actual", "in_iqr", "closed20_actual", "runup_vs_imoex_actual", "resolved_at"]


def read(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))
    except FileNotFoundError:
        return []


def write(path, rows):
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS, delimiter=";"); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def shrink(x_t, n, x_pool, a=A):
    w = n / (n + a)
    return w * x_t + (1 - w) * x_pool


def quant(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def cmd_make(a):
    hist = json.load(open(a["--history"], encoding="utf-8"))
    pool = [e for tk, r in hist.items() if not tk.startswith("_") for e in r["events"]]
    if len(pool) < 100:
        sys.exit("ОТКАЗ: в истории меньше 100 событий — общие частоты ненадёжны.")
    ok20 = [e for e in pool if e["days_observed"] >= 20]
    p0 = sum(1 for e in ok20 if e["days_to_close"] and e["days_to_close"] <= 20) / len(ok20)
    g = [e["gap_open_frac"] for e in pool]
    g25, g50, g75 = quant(g, .25), quant(g, .5), quant(g, .75)
    pr0 = sum(1 for e in pool if e["runup20_vs_imoex_pct"] > 0) / len(pool)
    log = read(a["--log"]); have = {r["key"] for r in log}
    now = datetime.now().replace(microsecond=0).isoformat(); today = date.today().isoformat(); added = 0
    for tk, r in hist.items():
        if tk.startswith("_"):
            continue
        ev = r["events"]; n = len(ev)
        for u in r["upcoming"]:
            key = f"{tk}_{u['t1']}"
            if key in have:
                continue  # запись неизменна: прогноз, сделанный раньше, не переписывается
            tg = [e["gap_open_frac"] for e in ev]
            o20 = [e for e in ev if e["days_observed"] >= 20]
            k = sum(1 for e in o20 if e["days_to_close"] and e["days_to_close"] <= 20)
            t_med = quant(tg, .5) if tg else g50
            row = dict(key=key, made_at=now, ticker=tk, t1=u["t1"], record=u["record"], dps=u["dps"], price=r.get("last_price") or "",
                       status=a.get("--status", "не указан"), model=MODEL, n_ticker=n,
                       gap_open_med=round(shrink(t_med, n, g50), 3),
                       gap_open_q25=round(shrink(quant(tg, .25) if tg else g25, n, g25), 3),
                       gap_open_q75=round(shrink(quant(tg, .75) if tg else g75, n, g75), 3),
                       base_gap_open_med=round(g50, 3),
                       p_close20=round((k + A * p0) / (len(o20) + A), 3), base_p_close20=round(p0, 3))
            # «рост до отсечки» прогнозируем только если окно из 20 торговых дней ещё не началось (иначе это уже факт, а не прогноз)
            days_left = (date.fromisoformat(u["t1"]) - date.today()).days
            if days_left >= 29:  # ≈ 20 торговых дней
                tr = [1 if e["runup20_vs_imoex_pct"] > 0 else 0 for e in ev]
                row["p_runup_pos"] = round((sum(tr) + A * pr0) / (len(tr) + A), 3); row["runup_window_started"] = "нет"
            else:
                row["runup_window_started"] = "да"
            log.append(row); added += 1
            print(f"+ {key}: гэп открытия {row['gap_open_med']} дивиденда [{row['gap_open_q25']}–{row['gap_open_q75']}], "
                  f"P(закрыт ≤20 дн) {row['p_close20']} (общая {row['base_p_close20']}), n тикера {n}")
    write(a["--log"], log); print(f"Добавлено прогнозов: {added}; всего в журнале: {len(log)}")


def cmd_resolve(a):
    log = read(a["--log"]); done = 0
    ix = None
    for r in log:
        if r["resolved_at"]:
            continue
        if r["t1"] >= date.today().isoformat():
            continue  # событие ещё не произошло
        since = (date.fromisoformat(r["t1"]) - timedelta(days=60)).isoformat()
        P = H.iss_hist(f"shares/boards/TQBR/securities/{r['ticker']}", since)
        dts = [x[0] for x in P]
        if r["t1"] not in dts:
            print(f"? {r['key']}: нет цены в день t1 — не разрешён"); continue
        k = dts.index(r["t1"])
        if k + 1 >= len(P):
            continue  # дня гэпа ещё нет
        D = float(r["dps"]); C0 = P[k][4]; O1 = P[k + 1][1]
        actual = (C0 - O1) / D
        r["gap_open_actual"] = round(actual, 3)
        r["in_iqr"] = int(float(r["gap_open_q25"]) <= actual <= float(r["gap_open_q75"]))
        if len(P) - 1 - k >= 20:
            r["closed20_actual"] = int(any(P[j][4] >= C0 for j in range(k + 1, k + 21)))
        else:
            r["closed20_actual"] = ""  # пока не известно; разрешится при следующем запуске
        if r["runup_window_started"] == "нет" and k >= 20:
            if ix is None:
                ix = {x[0]: x[1] for x in H.iss_hist("index/securities/IMOEX", "2026-01-01", "TRADEDATE,CLOSE")}
            if dts[k] in ix and dts[k - 20] in ix:
                r["runup_vs_imoex_actual"] = round(100 * ((C0 / P[k - 20][4] - 1) - (ix[dts[k]] / ix[dts[k - 20]] - 1)), 2)
        if r["closed20_actual"] != "" or len(P) - 1 - k >= 20:
            r["resolved_at"] = datetime.now().replace(microsecond=0).isoformat(); done += 1
        else:
            r["resolved_at"] = ""  # гэп известен, закрытие за 20 дней — нет: строка останется открытой до полного разрешения
        print(f"= {r['key']}: гэп {r['gap_open_actual']} (прогноз {r['gap_open_med']} [{r['gap_open_q25']}–{r['gap_open_q75']}]), закрыт ≤20 дн: {r['closed20_actual'] or 'ещё неизвестно'}")
    write(a["--log"], log); print(f"Разрешено строк: {done}")


def cmd_score(a):
    log = [r for r in read(a["--log"]) if r["resolved_at"]]
    n = len(log)
    print(f"Разрешённых прогнозов: {n}")
    if not n:
        return
    iqr = [int(r["in_iqr"]) for r in log if r["in_iqr"] != ""]
    print(f"Интервал [q25–q75] накрыл факт: {sum(iqr)} из {len(iqr)} ({sum(iqr) / max(len(iqr), 1):.0%}); ожидается около 50%")
    mae = [abs(float(r["gap_open_actual"]) - float(r["gap_open_med"])) for r in log]
    mae0 = [abs(float(r["gap_open_actual"]) - float(r["base_gap_open_med"])) for r in log]
    print(f"Ошибка гэпа (среднее |факт − прогноз|): модель {sum(mae) / n:.3f} против общего медианного {sum(mae0) / n:.3f}")
    bs = [(float(r["p_close20"]) - int(r["closed20_actual"])) ** 2 for r in log if r["closed20_actual"] != ""]
    b0 = [(float(r["base_p_close20"]) - int(r["closed20_actual"])) ** 2 for r in log if r["closed20_actual"] != ""]
    if bs:
        B, B0 = sum(bs) / len(bs), sum(b0) / len(b0)
        print(f"Brier закрытия ≤20 дн: модель {B:.3f}, общая частота {B0:.3f}, навык {1 - B / B0:+.1%} (n={len(bs)})")
    if n < 30:
        print(f"⚠️ n = {n} < 30: выводить о качестве прогнозов нельзя, только копить журнал.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("make", "resolve", "score"):
        sys.exit(__doc__)
    cmd, a, i = sys.argv[1], {"--log": "predictions.csv", "--history": "div_candidates.json"}, 2
    while i < len(sys.argv):
        a[sys.argv[i]] = sys.argv[i + 1]; i += 2
    {"make": cmd_make, "resolve": cmd_resolve, "score": cmd_score}[cmd](a)


if __name__ == "__main__":
    main()
