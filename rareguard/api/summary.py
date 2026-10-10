"""一页纸就诊摘要渲染（纯函数，spec §4.1 第 4 屏 / §5 离线可演示）。

风险结论仍来自规则引擎；此处只做展示层翻译，注入固定免责声明。
"""
from html import escape
_LAB_LABELS = {
    "ferritin": "血清铁蛋白", "crp": "C反应蛋白", "esr": "血沉",
    "plt": "血小板计数", "fib": "纤维蛋白原", "alt": "谷丙转氨酶",
    "tg": "甘油三酯", "temp": "体温",
}


def latest_labs(store, pid, as_of):
    rows = store.rows(
        "SELECT code,value,ref_low,ref_high,date FROM lab "
        "WHERE pid=? AND date<=? ORDER BY date DESC", (pid, as_of))
    seen, out = set(), []
    for code, value, lo, hi, _date in rows:
        if code in seen:
            continue
        seen.add(code)
        abnormal = (lo is not None and value < lo) or (
            hi is not None and value > hi)
        out.append({"code": code, "value": value,
                    "ref_low": lo, "ref_high": hi, "abnormal": abnormal})
    return out


def _trend_svg(points):
    if not points:
        return ""
    w, h, pad = 560, 140, 12
    n = max(len(points) - 1, 1)
    xs = [pad + (w - 2 * pad) * i / n for i in range(len(points))]
    ys = [h - pad - (h - 2 * pad) * min(p["score"], 100) / 100
          for p in points]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    y40 = h - pad - (h - 2 * pad) * 0.4
    y70 = h - pad - (h - 2 * pad) * 0.7
    return (f'<svg viewBox="0 0 {w} {h}" width="100%">'
            f'<line x1="{pad}" y1="{y40:.1f}" x2="{w - pad}" y2="{y40:.1f}" '
            f'stroke="#e0a800"/>'
            f'<line x1="{pad}" y1="{y70:.1f}" x2="{w - pad}" y2="{y70:.1f}" '
            f'stroke="#c0392b"/>'
            f'<polyline points="{line}" fill="none" stroke="#2c6e91" '
            f'stroke-width="2"/></svg>')


def render_summary_html(pid, name, as_of, level, score, hits, points, labs):
    pid, name, as_of = (escape(str(v)) for v in (pid, name, as_of))
    color = {"red": "#c0392b", "yellow": "#e0a800"}.get(level, "#2e7d32")
    level_cn = {"red": "红（高危）", "yellow": "黄（警戒）"}.get(level, "绿（观察）")

    def _num(v):
        return "" if v is None else f"{v:g}"

    rows = "".join(
        f'<tr style="{"background:#fdecea" if l["abnormal"] else ""}">'
        f'<td>{escape(str(_LAB_LABELS.get(l["code"], l["code"])))}</td>'
        f'<td>{_num(l["value"])}</td>'
        f'<td>{_num(l["ref_low"])} - {_num(l["ref_high"])}</td></tr>'
        for l in labs)
    hitlist = "".join(f"<li>{escape(str(h['name']))}（{escape(str(h['level']))}）</li>" for h in hits)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>就诊摘要 {name}</title><style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px;
color:#222}}h1{{font-size:20px}}.badge{{display:inline-block;
padding:4px 12px;border-radius:6px;color:#fff;background:{color}}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #ccc;padding:6px 8px;font-size:14px;
text-align:left}}footer{{margin-top:20px;font-size:13px;color:#555}}
@media print{{footer{{position:fixed;bottom:0}}}}</style></head><body>
<h1>RareGuard 就诊一页纸摘要</h1>
<p>患儿：<b>{name}</b>（{pid}）&nbsp; 评估日期：{as_of} &nbsp;
风险等级：<span class="badge">{level_cn}</span> &nbsp; MAS 风险分：{score}</p>
<p>近 90 天风险分趋势（黄线 40 / 红线 70）：</p>
{_trend_svg(points)}
<h3>关键化验异常值</h3>
<table><tr><th>项目</th><th>结果</th><th>参考区间</th></tr>{rows}</table>
<h3>命中预警规则</h3><ul>{hitlist or "<li>无</li>"}</ul>
<footer>AI 辅助参考，请以医生诊断为准。本摘要不构成诊断或用药建议。</footer>
</body></html>"""
