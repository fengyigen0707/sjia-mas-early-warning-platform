"""指标别名与单位归一化：OCR/手工录入入库前的统一契约。"""
import math


class UnknownIndicator(Exception):
    pass


class UnitConversionError(Exception):
    pass


ALIASES = {
    "血清铁蛋白": "ferritin", "铁蛋白": "ferritin", "ferritin": "ferritin",
    "c反应蛋白": "crp", "crp": "crp",
    "血沉": "esr", "红细胞沉降率": "esr", "esr": "esr",
    "血小板": "plt", "血小板计数": "plt", "plt": "plt",
    "纤维蛋白原": "fib", "fib": "fib",
    "谷丙转氨酶": "alt", "丙氨酸氨基转移酶": "alt", "alt": "alt",
    "谷草转氨酶": "ast", "天门冬氨酸氨基转移酶": "ast", "ast": "ast",
    "甘油三酯": "tg", "tg": "tg",
    "体温": "temp", "temp": "temp",
    # 英文全称（真实模型 OCR 常回英文项目名；查表前统一 lower()）
    "serum ferritin": "ferritin",
    "c-reactive protein": "crp",
    "erythrocyte sedimentation rate": "esr",
    "platelet count": "plt",
    "fibrinogen": "fib",
    "alanine aminotransferase": "alt",
    "aspartate aminotransferase": "ast",
    "triglycerides": "tg", "triglyceride": "tg",
}

# code -> {归一化单位键: (乘系数, 加偏移)}；规范单位见各 code 第一项
UNITS: dict[str, dict[str, tuple[float, float]]] = {
    "ferritin": {"ng/ml": (1, 0), "μg/l": (1, 0), "ug/l": (1, 0)},
    "crp": {"mg/l": (1, 0), "mg/dl": (10, 0)},
    "esr": {"mm/h": (1, 0)},
    "plt": {"10^9/l": (1, 0), "g/l": (1, 0), "×10⁹/l": (1, 0)},
    "fib": {"g/l": (1, 0), "mg/l": (0.001, 0), "mg/dl": (0.01, 0)},
    "alt": {"u/l": (1, 0), "iu/l": (1, 0)},
    "ast": {"u/l": (1, 0), "iu/l": (1, 0)},
    "tg": {"mmol/l": (1, 0), "mg/dl": (1 / 88.57, 0)},
    "temp": {"℃": (1, 0), "c": (1, 0), "°f": (1 / 1.8, -32 / 1.8)},
}


def normalize(code_or_alias: str, value: float, unit: str) -> tuple[str, float]:
    """返回 (规范代码, 规范单位下的数值)。"""
    key = code_or_alias.strip()
    code = ALIASES.get(key) or ALIASES.get(key.lower())
    if code is None:
        raise UnknownIndicator(code_or_alias)
    unit_key = unit.strip().lower()
    table = UNITS[code]
    if unit_key not in table:
        raise UnitConversionError(f"{code}: {unit}")
    factor, offset = table[unit_key]
    converted = value * factor + offset
    if not math.isfinite(converted) or converted < 0:
        raise ValueError("指标必须为非负有限数值")
    return code, converted
