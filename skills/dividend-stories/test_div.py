# Тесты расчёта «купить до» (T+1/T+2) на известных случаях. Запуск: python test_div.py
import div_history as H
from datetime import date, timedelta

def wd(start, end, holidays=()):
    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out

td = wd("2023-01-01", "2026-12-31")
cases = [
    ("отсечка в воскресенье 13.12.2026 → купить до четверга 10.12 (пример из скилла)", "2026-12-13", "2026-12-10"),
    ("отсечка в понедельник 20.07.2026 → пятница 17.07 (SBER)", "2026-07-20", "2026-07-17"),
    ("отсечка в пятницу 18.07.2025 → четверг 17.07 (SBER)", "2025-07-18", "2025-07-17"),
    ("отсечка в среду 11.07.2024 → вторник 09.07? нет: расчёт S=11.07, D=10.07 (SBER t1 2024-07-10)", "2024-07-11", "2024-07-10"),
    ("T+2 до 31.07.2023: отсечка в пятницу 13.01.2023 → вторник 10.01? D = 2 торговых дня до S", "2023-01-13", "2023-01-11"),
]
bad = 0
for name, rec, want in cases:
    got = H.expected_t1(rec, td)
    ok = got == want; bad += not ok
    print(("OK  " if ok else "FAIL"), name, "→", got, "" if ok else f"(ожидалось {want})")
# праздник между: отсечка вт 05.05.2026, праздники 01–03.05 и 09.05; торги 04.05 (пн) есть
td2 = wd("2026-04-20", "2026-05-31", holidays=("2026-05-01", "2026-05-11"))
got = H.expected_t1("2026-05-12", td2); want = "2026-05-08"
print(("OK  " if got == want else "FAIL"), "праздник 11.05 (пн): отсечка 12.05 → расчёт S=12.05, D=ближайший торговый день до S =", got); bad += got != want
print("future beyond calendar:", H.expected_t1("2027-01-10", td))
print("ИТОГО ошибок:", bad); raise SystemExit(bad)
