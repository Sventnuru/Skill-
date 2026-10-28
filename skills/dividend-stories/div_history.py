# История дивидендных отсечек по бумагам Мосбиржи: гэп, закрытие гэпа, «ловля» дивиденда, рост перед отсечкой.
# Версия 2 (28.10.2026): учёт пропущенных событий, проверка «купить до» по правилу T+1/T+2, ДИ по годам (кластеры).
# Данные: выплаты — smart-lab.ru/q/<TICKER>/dividend/ (T-1 = последний день покупки, дата отсечки, дивиденд);
#         цены — MOEX ISS (режим TQBR, нескорректированные), индекс — IMOEX.
# Запуск: python div_history.py SBER LKOH MTSS [--tax 0.13] [--cost 0.001] [--since 2015-01-01] [--out res.json]
import sys, re, json, math, random, time
from datetime import datetime, date, timedelta
import requests  # одно соединение на сайт: частые новые TLS-рукопожатия с ISS подвисают
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
S = {}
T1_FROM = "2023-07-31"  # с этой даты расчёты T+1; раньше T+2


def get(url, tries=4):
    host = url.split("/")[2]
    if host not in S:
        S[host] = requests.Session(); S[host].headers["User-Agent"] = "div-history/2.0" if "moex.com" in host else UA
    for t in range(tries):
        try:
            r = S[host].get(url, timeout=20); r.raise_for_status(); r.encoding = "utf-8"; return r.text
        except Exception:
            if t == tries - 1:
                raise
            time.sleep(3 * (t + 1))


def payouts(tk, page=None):
    page = page or tk
    try:
        html = get(f"https://smart-lab.ru/q/{page}/dividend/")
    except Exception:
        html = ""
    if not html and page == tk and tk.endswith("P"):
        return payouts(tk, tk[:-1])  # привилегированные (SBERP, SNGSP…) перечислены на странице обыкновенных
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    out = {}
    # строка таблицы: ТИКЕР T-1 отсечка период дивиденд ₽ цена доходность%   (период: «3кв 2026», «2023 год», «2021 н/расп»)
    pat = (rf"\b{tk} (\d\d\.\d\d\.\d{{4}}) (\d\d\.\d\d\.\d{{4}}) (\S+(?: (?:\d{{4}}|год|н/расп))?) "
           rf"(\d{{1,3}}(?: \d{{3}})*(?:,\d+)?) ₽ (\d+(?:,\d+)?) (\d+(?:,\d+)?)%")
    for m in re.finditer(pat, txt):
        t1, rec, per, d, px, yl = m.groups()
        o = dict(t1=datetime.strptime(t1, "%d.%m.%Y").date().isoformat(), record=datetime.strptime(rec, "%d.%m.%Y").date().isoformat(),
                 period=per, dps=float(d.replace(" ", "").replace(",", ".")), px_sl=float(px.replace(",", ".")))
        out[(o["t1"], o["dps"])] = o
    if not out and page == tk and tk.endswith("P"):
        return payouts(tk, tk[:-1])
    return sorted(out.values(), key=lambda o: o["t1"])


def split_factor(c0, px_sl):
    """smart-lab пересчитывает старые выплаты и цены на сплит, ISS — нет: множитель для дивиденда."""
    r = c0 / px_sl if px_sl else 1.0
    if 0.7 <= r <= 1.43:  # цена smart-lab может быть с другого дня: допуск шире 25%, но уже минимального сплита ×2
        return 1.0
    for f in (2, 3, 4, 5, 10, 20, 25, 50, 100, 200, 500, 1000):
        for g in (f, 1 / f):
            if abs(r / g - 1) < 0.2:
                return g
    return None  # не похоже ни на сплит, ни на ту же цену — событие отбрасывается


def iss_hist(path, since, cols="TRADEDATE,OPEN,HIGH,LOW,CLOSE"):
    rows, start = [], 0
    while True:
        j = json.loads(get(f"https://iss.moex.com/iss/history/engines/stock/markets/{path}.json?from={since}&start={start}"
                           f"&iss.meta=off&history.columns={cols}"))
        rows += j["history"]["data"]; idx, tot, ps = j["history.cursor"]["data"][0]
        start = idx + ps
        if start >= tot:
            break
    return [r for r in rows if r[-1] is not None]


def market_now(tk):
    """Последняя цена и средний дневной оборот за 20 торговых дней (млн ₽), режим TQBR."""
    j = json.loads(get(f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{tk}.json"
                       f"?iss.meta=off&iss.only=marketdata&marketdata.columns=LAST,LCLOSEPRICE"))
    row = (j["marketdata"]["data"] or [[None, None]])[0]
    v = iss_hist(f"shares/boards/TQBR/securities/{tk}", (date.today() - timedelta(days=45)).isoformat(),
                 "TRADEDATE,VALUE")[-20:]
    return row[0] or row[1], round(sum(r[1] for r in v) / len(v) / 1e6, 1) if v else None


def expected_t1(record, tdays):
    """Последний день покупки по календарю торгов: расчёты по сделке дня D проходят через 1 торговый день (T+1, с 31.07.2023)
    или через 2 (T+2, раньше) и должны успеть не позже даты отсечки. tdays — отсортированные даты торгов (ISO).
    Для дат за пределом календаря возвращает None (будущее: праздники неизвестны — проверить у биржи/брокера)."""
    if not tdays or record > tdays[-1]:
        return None
    lag = 1 if record >= T1_FROM else 2
    k = max(i for i, d in enumerate(tdays) if d <= record)  # S = последний торговый день ≤ отсечки (день расчётов)
    return tdays[k - lag] if k - lag >= 0 else None


def q(a, p):
    a = sorted(x for x in a if x is not None); return a[min(len(a) - 1, int(p * len(a)))] if a else None


def mean(a):
    return sum(a) / len(a) if a else float("nan")


def events_for(tk, since, ix, tax, cost, pay=None, P=None):
    """События тикера: (список, пропущенные [{t1, reason}], число выплат). Ничего не теряется молча."""
    P = P or iss_hist(f"shares/boards/TQBR/securities/{tk}", since); pay = pay if pay is not None else payouts(tk)
    dts = [r[0] for r in P]; pos = {d: k for k, d in enumerate(dts)}
    tdays = sorted(ix)
    ev, skipped = [], []
    for o in pay:
        if o["t1"] >= date.today().isoformat():
            continue  # будущие выплаты — в upcoming, не в историю
        k = pos.get(o["t1"])
        if k is None:
            skipped.append(dict(t1=o["t1"], reason=("«купить до» агрегатора — не торговый день по календарю IMOEX" if o["t1"] >= since and o["t1"] not in ix else "нет цены ISS в день t1 (до начала истории или нет торгов)"))); continue
        if k < 20:
            skipped.append(dict(t1=o["t1"], reason="меньше 20 торговых дней истории до t1")); continue
        if k + 1 >= len(P):
            skipped.append(dict(t1=o["t1"], reason="нет дня гэпа в истории")); continue
        C0 = P[k][4]; sf = split_factor(C0, o["px_sl"])
        if sf is None:
            skipped.append(dict(t1=o["t1"], reason=f"цена ISS {C0} не согласуется с ценой smart-lab {o['px_sl']}")); continue
        D = o["dps"] * sf; O1, C1 = P[k + 1][1], P[k + 1][4]
        days = next((j - k for j in range(k + 1, min(len(P), k + 251)) if P[j][4] >= C0), None)
        ru = C0 / P[k - 20][4] - 1
        rix = ix[dts[k]] / ix[dts[k - 20]] - 1 if dts[k] in ix and dts[k - 20] in ix else math.nan
        exp = expected_t1(o["record"], tdays)
        # 250 торговых дней после gap_day ещё не прошло → «не закрыт» неизвестно, а не «нет»
        horizon = len(P) - 1 - k
        e = dict(o, C0=C0, gap_day=dts[k + 1], yield_pct=round(100 * D / C0, 2),
                 gap_open_frac=round((C0 - O1) / D, 3), gap_close_frac=round((C0 - C1) / D, 3),
                 capture_net_pct=round(100 * ((C1 - C0 + (1 - tax) * D) / C0 - cost), 3),
                 days_to_close=days, days_observed=horizon, runup20_pct=round(100 * ru, 2),
                 runup20_vs_imoex_pct=round(100 * (ru - rix), 2),
                 t1_expected=exp, t1_ok=(None if exp is None else exp == o["t1"]))
        ev.append(e)
    return ev, skipped, len(pay), P


def year_cluster_ci(items, key, n=4000, seed=7):
    """Среднее и 95% ДИ с повторной выборкой по годам отсечки (события одного года зависимы)."""
    by = {}
    for e in items:
        v = e.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        by.setdefault(e["record"][:4], []).append(v)
    ys = sorted(by)
    if len(ys) < 3:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(n):
        vals = [x for y in (rnd.choice(ys) for _ in ys) for x in by[y]]
        means.append(sum(vals) / len(vals))
    means.sort()
    allv = [x for y in ys for x in by[y]]
    return dict(mean=round(mean(allv), 3), ci95=[round(means[int(0.025 * n)], 3), round(means[int(0.975 * n)], 3)],
                years=len(ys), by_year={y: round(mean(by[y]), 2) for y in ys}, n=len(allv))


def main():
    a = sys.argv[1:]; opt = {"--tax": "0.13", "--cost": "0.001", "--since": "2015-01-01", "--out": "div_history.json"}; tks = []; i = 0
    while i < len(a):
        if a[i] in opt:
            opt[a[i]] = a[i + 1]; i += 2
        else:
            tks.append(a[i].upper()); i += 1
    tax, cost, since = float(opt["--tax"]), float(opt["--cost"]), opt["--since"]
    ix = {r[0]: r[1] for r in iss_hist("index/securities/IMOEX", since, "TRADEDATE,CLOSE")}
    res, pool, ctrl = {}, [], []
    skipped_all, payouts_total = [], 0
    random.seed(1)
    for tk in tks:
        try:
            ev, skipped, npay, P = events_for(tk, since, ix, tax, cost)
            pay = payouts(tk)
        except Exception as e:
            print(f"{tk}: пропущен — {repr(e)[:120]}", flush=True); skipped_all.append(dict(tk=tk, reason="тикер не загружен: " + repr(e)[:80])); continue
        payouts_total += npay
        for s in skipped:
            skipped_all.append(dict(tk=tk, **s)); print(f"  {tk} {s['t1']}: пропуск — {s['reason']}", flush=True)
        dts = [r[0] for r in P]; pos = {d: k for k, d in enumerate(dts)}
        for e in ev:
            e["tk"] = tk
            if e["t1_ok"] is False and e["record"] >= T1_FROM:
                print(f"  {tk} {e['t1']}: ⚠️ «купить до» smart-lab {e['t1']} ≠ расчёт T+1 {e['t1_expected']} (отсечка {e['record']})", flush=True)
        pool += ev
        near = set()
        for e in ev:
            k = pos[e["t1"]]; near.update(range(k - 30, k + 31))
        for k in range(20, len(P)):  # отрицательный контроль: 20-дневные окна вдали от отсечек
            if k not in near and dts[k] in ix and dts[k - 20] in ix:
                ctrl.append(100 * ((P[k][4] / P[k - 20][4] - 1) - (ix[dts[k]] / ix[dts[k - 20]] - 1)))
        n = max(len(ev), 1); closed = [e["days_to_close"] for e in ev if e["days_to_close"]]
        # доля закрытий считается только по событиям, у которых прошло достаточно времени (иначе «не закрыт» — это «ещё не известно»)
        ok20 = [e for e in ev if e["days_observed"] >= 20]; ok250 = [e for e in ev if e["days_observed"] >= 250]
        try:
            last, adv = market_now(tk)
        except Exception:
            last, adv = None, None
        today = date.today().isoformat()
        r = res[tk] = dict(n=len(ev), n_payouts=npay, n_skipped=len(skipped), last_price=last, adv20_mln_rub=adv,
                           upcoming=[o for o in pay if o["t1"] >= today],  # ближайшие выплаты по smart-lab (сверить с календарём и эмитентом)
                           median_gap_open_frac=q([e["gap_open_frac"] for e in ev], 0.5),
                           median_gap_close_frac=q([e["gap_close_frac"] for e in ev], 0.5),
                           share_closed_20d=round(sum(1 for e in ok20 if e["days_to_close"] and e["days_to_close"] <= 20) / max(len(ok20), 1), 2),
                           n_for_20d=len(ok20),
                           share_closed_250d=round(sum(1 for e in ok250 if e["days_to_close"]) / max(len(ok250), 1), 2), n_for_250d=len(ok250),
                           median_days_to_close=q(closed, 0.5), mean_capture_net_pct=round(mean([e["capture_net_pct"] for e in ev]), 3),
                           mean_runup20_vs_imoex_pct=round(mean([e["runup20_vs_imoex_pct"] for e in ev if not math.isnan(e["runup20_vs_imoex_pct"])]), 2),
                           events=ev)
        for u in r["upcoming"]:
            exp = expected_t1(u["record"], sorted(ix))
            print(f"{tk}: БЛИЖАЙШАЯ — купить до {u['t1']}" + ("" if exp is None else f" (расчёт T+1: {exp})") + f", отсечка {u['record']}, {u['period']}, {u['dps']} ₽"
                  + (f", доходность {100 * u['dps'] / last:.2f}% до НДФЛ, {100 * (1 - tax) * u['dps'] / last:.2f}% после" if last else ""), flush=True)
        print(f"{tk}: цена {last}, оборот {adv} млн ₽/день | отсечек {r['n']} из {npay} | гэп открытие/дивиденд медиана {r['median_gap_open_frac']} | закрыт ≤20 дн {r['share_closed_20d']:.0%} (n={r['n_for_20d']}), "
              f"≤250 дн {r['share_closed_250d']:.0%} (n={r['n_for_250d']}), медиана {r['median_days_to_close']} дн | ловля нетто {r['mean_capture_net_pct']}% | "
              f"рост 20 дн до T-1 сверх IMOEX {r['mean_runup20_vs_imoex_pct']}%", flush=True)
    if pool:
        ru = [e["runup20_vs_imoex_pct"] for e in pool if not math.isnan(e["runup20_vs_imoex_pct"])]
        cap = [e["capture_net_pct"] for e in pool]; m = mean(cap)
        sd = (sum((c - m) ** 2 for c in cap) / max(len(cap) - 1, 1)) ** 0.5
        sims = [mean([random.choice(ctrl) for _ in ru]) for _ in range(2000)]
        common = [e for e in pool if not e["tk"].endswith("P")]  # без привилегированных: пары SBER/SBERP, TATN/TATNP почти дубли
        reasons = {}
        for s in skipped_all:
            k = s["reason"].split(" ")[0] + " " + " ".join(s["reason"].split(" ")[1:3]); reasons[k] = reasons.get(k, 0) + 1
        res["_summary"] = s = dict(
            payouts_total=payouts_total, events=len(pool), skipped=len(skipped_all), skipped_reasons=reasons, tickers=len(tks),
            t1_mismatch=sum(1 for e in pool if e["t1_ok"] is False and e["record"] >= T1_FROM),
            capture_net_mean_pct=round(m, 3),
            capture_net_ci95_naive=[round(m - 1.96 * sd / len(cap) ** 0.5, 3), round(m + 1.96 * sd / len(cap) ** 0.5, 3)],
            capture_net_year_cluster=year_cluster_ci(pool, "capture_net_pct"),
            gap_open_frac_median=q([e["gap_open_frac"] for e in pool], 0.5), gap_close_frac_median=q([e["gap_close_frac"] for e in pool], 0.5),
            runup20_vs_imoex_mean_pct=round(mean(ru), 2), control_windows_mean_pct=round(mean(ctrl), 2),
            runup_p_one_sided_naive=round(sum(1 for x in sims if x >= mean(ru)) / len(sims), 4),
            runup_year_cluster=year_cluster_ci(pool, "runup20_vs_imoex_pct"),
            runup_common_only_year_cluster=year_cluster_ci(common, "runup20_vs_imoex_pct"),
            runup_share_positive=round(sum(1 for x in ru if x > 0) / len(ru), 3))
        print("ИТОГО:", json.dumps(s, ensure_ascii=False), flush=True)
    json.dump(res, open(opt["--out"], "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
