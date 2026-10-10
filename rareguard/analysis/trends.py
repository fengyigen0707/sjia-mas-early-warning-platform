"""R-B 趋势规则：连续变化须超过最小步幅，抗基线噪声误报。"""
from datetime import date

from rareguard.analysis.rules import RuleHit


def _ratios(vals):
    return [b / a for a, b in zip(vals, vals[1:])]


def _rising(vals, step_min, last_min) -> bool:
    if len(vals) < 3 or vals[-1] < last_min or any(v <= 0 for v in vals[-3:]):
        return False
    return all(r >= 1 + step_min for r in _ratios(vals[-3:]))


def _falling_tail(vals, step_max, n) -> bool:
    if len(vals) < n + 1 or any(v <= 0 for v in vals[-(n + 1):]):
        return False
    return all(r <= 1 - step_max for r in _ratios(vals[-(n + 1):]))


def _days_between(d1: str, d2: str) -> int:
    return (date.fromisoformat(d2) - date.fromisoformat(d1)).days


def eval_trend(series: dict) -> list:
    hits: list[RuleHit] = []

    def vals(code):
        return [p.value for p in series.get(code, [])]

    v = vals("crp")
    if _rising(v, 0.15, 30):
        hits.append(RuleHit("B1", "CRP连续上升", "yellow", {"last": v[-1]}))

    f = vals("ferritin")
    if _rising(f, 0.15, 400):
        hits.append(RuleHit("B2", "铁蛋白连续上升", "yellow", {"last": f[-1]}))
    pts = series.get("ferritin", [])
    for j in range(len(pts)):
        if pts[j].value < 400:
            continue
        for i in range(j):
            if _days_between(pts[i].date, pts[j].date) <= 14 and \
                    pts[i].value > 0 and pts[j].value / pts[i].value >= 2:
                hits.append(RuleHit(
                    "B5", "铁蛋白14天内翻倍", "red",
                    {"from": pts[i].value, "to": pts[j].value,
                     "date": pts[j].date}))
                break
        else:
            continue
        break

    p = vals("plt")
    if len(p) >= 3 and all(v > 0 for v in p[-3:]) and p[-1] < 150 and all(r <= 0.92 for r in _ratios(p[-3:])):
        hits.append(RuleHit("B3", "血小板连续下降", "yellow", {"last": p[-1]}))

    fb = vals("fib")
    if _falling_tail(p, 0.08, 2) and _falling_tail(fb, 0.10, 2):
        hits.append(RuleHit("B4", "血小板+纤维蛋白原同向下降", "red",
                            {"plt": p[-1], "fib": fb[-1]}))
    return hits
