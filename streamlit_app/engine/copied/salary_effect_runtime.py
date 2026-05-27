# -*- coding: utf-8 -*-
"""
低工资折减招聘人数：线上仅用「当回合状态 + CONFIG 系数」，与 scripts/salary_effect_global_fit 中 unified 形状一致。
系数应由策划按可获得变量与经济含义设定；勿把离线脚本对历史样本压 loss 的输出当作多人模型唯一真值。

三套公式（由 CONFIG / 城市表 / GAME_STATE 选择）:
  - linear:  int(N * pay / avg) 招聘；**辞职恒为 0**（与旧行为一致）
  - affine:  int(N * (pay−c) / (avg−c))（再 cap 到 N）；**c 由调用方传入**（场内规则：与 pay 截断同源的 CONFIG 工资下限）；avg≤c 或 pay≤c 时退化为安全分支；**辞职恒为 0**
  - unified: 招聘同 lm 乘子；辞职同上；**a0–a4、c0–c2 来自 CONFIG**（策划/离线拟合）。
  - unified_dynamic: **与 unified 同形**，但 **a、c 每回合由场内量 + CONFIG 锚点解析计算**（无逐场手填 ac）:
      锚点：`avg_baseline`（城市/全局基准均薪）、`salary_fluctuation`；场内：`avg`/`pay`/`prev`/`gap` 与 g、npr、lp。
      贴近均价区：当 **(avg−pay)/avg ≤ band**（默认约 1%）时，仅 **ceil(N×dynamic_fraction)**（默认 10%）套用 full dynamic 乘子，**其余名额满额录用**；band 外仍整段 N 走 dynamic。lm 内 **npr=min(n/max(prev,1), 2)**，避免 prev 极小时 npr 爆炸。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

LOW_WAGE_HIRE_LINEAR = "linear"
LOW_WAGE_HIRE_AFFINE = "affine"
LOW_WAGE_HIRE_UNIFIED = "unified"
LOW_WAGE_HIRE_UNIFIED_DYNAMIC = "unified_dynamic"


def normalize_low_wage_hire_formula(raw: Any) -> str:
    if raw is None:
        return LOW_WAGE_HIRE_LINEAR
    s = str(raw).strip().lower().replace("-", "_")
    if s in ("unified_dynamic", "unified_field", "unified_v2"):
        return LOW_WAGE_HIRE_UNIFIED_DYNAMIC
    if s == LOW_WAGE_HIRE_UNIFIED:
        return LOW_WAGE_HIRE_UNIFIED
    if s == LOW_WAGE_HIRE_AFFINE:
        return LOW_WAGE_HIRE_AFFINE
    return LOW_WAGE_HIRE_LINEAR


def _unified_family(formula: str) -> bool:
    f = normalize_low_wage_hire_formula(formula)
    return f in (LOW_WAGE_HIRE_UNIFIED, LOW_WAGE_HIRE_UNIFIED_DYNAMIC)


def _gap(avg_salary: float, pay: float) -> float:
    if avg_salary <= 0:
        return 0.0
    return max(0.0, (avg_salary - pay) / avg_salary)


def _coerce_unified_params(src: Any) -> Dict[str, float]:
    out = {f"a{i}": 0.0 for i in range(5)}
    if not isinstance(src, Mapping):
        return out
    for i in range(5):
        k = f"a{i}"
        try:
            out[k] = float(src.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


def _coerce_quit_coeffs(src: Any) -> Tuple[float, float, float]:
    """c0,c1,c2；缺省 c0=-8 使 sigmoid 接近 0，避免未配置 unified 时出现大量辞职。"""
    if not isinstance(src, Mapping):
        return (-8.0, 0.0, 0.0)

    def _f(key: str, default: float) -> float:
        try:
            return float(src.get(key, default) if src.get(key, default) is not None else default)
        except (TypeError, ValueError):
            return default

    return (_f("c0", -8.0), _f("c1", 0.0), _f("c2", 0.0))


def _field_unified_stretch(
    avg: float,
    *,
    avg_baseline: float,
    salary_fluctuation: float,
) -> Tuple[float, float]:
    """heat：当轮均价相对基准；inv_nu：波动相对均价的可比尺度（有上限）。"""
    base = max(float(avg_baseline), 1.0)
    heat = max(0.5, min(3.0, avg / base))
    fl = max(0.0, float(salary_fluctuation))
    nu = fl / max(avg, 1.0)
    inv_nu = min(6.0, 1.0 / (nu + 0.12))
    return heat, inv_nu


def _field_unified_hire_params(
    avg: float,
    pay: float,
    prev: int,
    n_req: int,
    *,
    avg_baseline: Optional[float],
    salary_fluctuation: Optional[float],
) -> Dict[str, float]:
    """由场内量生成与 unified 同形的 a0–a4（admin 静态 dict 在此模式下忽略）。"""
    pr = max(int(prev), 1)
    npr = max(0, int(n_req)) / pr
    ab = float(avg_baseline) if avg_baseline is not None else avg
    fl = (
        float(salary_fluctuation)
        if salary_fluctuation is not None
        else max(avg * 0.05, 100.0)
    )
    heat, inv_nu = _field_unified_stretch(avg, avg_baseline=ab, salary_fluctuation=fl)
    sh = max(math.sqrt(heat), 0.7)
    return {
        "a0": -0.03 * min(npr, 2.0),
        "a1": -0.4 * inv_nu / sh,
        "a2": -0.25 * heat,
        "a3": 0.45 + 0.15 * (heat - 1.0),
        "a4": 0.1,
    }


def _field_unified_quit_coeffs(
    avg: float,
    pay: float,
    prev: int,
    nd: int,
    *,
    avg_baseline: Optional[float],
    salary_fluctuation: Optional[float],
) -> Tuple[float, float, float]:
    pr = max(int(prev), 1)
    npr = max(0, int(nd)) / pr
    ab = float(avg_baseline) if avg_baseline is not None else avg
    fl = (
        float(salary_fluctuation)
        if salary_fluctuation is not None
        else max(avg * 0.05, 100.0)
    )
    heat, inv_nu = _field_unified_stretch(avg, avg_baseline=ab, salary_fluctuation=fl)
    sh = max(math.sqrt(heat), 0.7)
    c0 = -4.0 - 0.5 * (heat - 1.0)
    c1 = 5.0 * inv_nu / sh
    c2 = -0.6 * min(npr, 2.0)
    return (c0, c1, c2)


# unified / unified_dynamic 中 lm 使用的「相对扩编压力」n/pr 上限；避免 prev=0 时 npr=n 爆炸、lm→-∞、招聘被压成 0。
_UNIFIED_NPR_CAP_FOR_LM = 2.0
# unified_dynamic「贴近均价」混合区默认：相对缺口 ≤ band 时启用；区内仅 dynamic_fraction×N（向上取整）走 dynamic。
_UNIFIED_DYNAMIC_NEAR_AVG_BAND_REL_DEFAULT = 0.01
_UNIFIED_DYNAMIC_NEAR_AVG_DYNAMIC_FRAC_DEFAULT = 0.10


def _sigmoid(z: float) -> float:
    z = max(-40.0, min(40.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _clamp_near_avg_blend_params(
    band: Optional[float], frac: Optional[float]
) -> Tuple[float, float]:
    b = (
        float(_UNIFIED_DYNAMIC_NEAR_AVG_BAND_REL_DEFAULT)
        if band is None
        else float(band)
    )
    f = (
        float(_UNIFIED_DYNAMIC_NEAR_AVG_DYNAMIC_FRAC_DEFAULT)
        if frac is None
        else float(frac)
    )
    b = max(1e-6, min(0.5, b))
    f = max(0.0, min(1.0, f))
    return b, f


def _unified_lm_hire_effective(
    n: int,
    pay: float,
    avg: float,
    prev: int,
    p: Mapping[str, float],
) -> int:
    """已知 a0–a4，计算 min(N, floor(N×pay/avg×e^lm))。"""
    n = max(0, int(n))
    if n <= 0 or avg <= 0:
        return 0
    if pay >= avg - 1e-9:
        return n
    g = _gap(avg, pay)
    pr = max(int(prev), 1)
    npr = min(n / pr, _UNIFIED_NPR_CAP_FOR_LM)
    lp = math.log1p(float(max(0, int(prev)))) / 6.0
    lm = (
        float(p["a0"])
        + float(p["a1"]) * g
        + float(p["a2"]) * npr
        + float(p["a3"]) * g * npr
        + float(p["a4"]) * lp
    )
    lm = max(-6.0, min(5.0, lm))
    m = math.exp(lm)
    raw = n * pay / avg * m
    return min(n, max(0, int(math.floor(raw + 1e-9))))


def _affine_hire_effective(
    n: int,
    pay: float,
    avg: float,
    c: float,
) -> int:
    """N * (pay-c)/(avg-c) 向下取整并 cap 到 n；avg≤c 或 pay≤c 时退回 linear / 0。"""
    if avg <= c + 1e-12:
        return int(n * (pay / avg)) if avg > 0 else 0
    if pay <= c + 1e-12:
        return 0
    den = avg - c
    if den <= 1e-12:
        return int(n * (pay / avg)) if avg > 0 else 0
    ratio = (pay - c) / den
    if ratio >= 1.0 - 1e-12:
        return n
    raw = n * ratio
    return min(n, max(0, int(math.floor(raw + 1e-9))))


def resolve_low_wage_hire_formula(
    *,
    config: Mapping[str, Any],
    city_cfg: Optional[Mapping[str, Any]] = None,
    game_state: Optional[Mapping[str, Any]] = None,
) -> str:
    """优先级: GAME_STATE['low_wage_hire_formula'] > 城市表 > CONFIG['low_wage_hire_formula'] > linear。"""
    if game_state:
        gs = game_state.get("low_wage_hire_formula")
        if gs is not None and str(gs).strip():
            return normalize_low_wage_hire_formula(gs)
    if city_cfg:
        cv = city_cfg.get("low_wage_hire_formula")
        if cv is not None and str(cv).strip():
            return normalize_low_wage_hire_formula(cv)
    return normalize_low_wage_hire_formula(config.get("low_wage_hire_formula"))


def compute_low_wage_new_effective(
    new_requested: int,
    pay: float,
    avg_this_round: float,
    prev_for_role: int,
    formula: str,
    unified_params: Mapping[str, float],
    *,
    affine_floor: Optional[float] = None,
    unified_field_avg_baseline: Optional[float] = None,
    unified_field_salary_fluctuation: Optional[float] = None,
    unified_dynamic_near_avg_band_rel: Optional[float] = None,
    unified_dynamic_near_avg_dynamic_fraction: Optional[float] = None,
) -> int:
    """
    在「增员」分支下调用：pay >= avg 时满额；否则按公式折减。
    pay / avg 已在外层做过 min/max 截断。
    """
    n = max(0, int(new_requested))
    if n <= 0:
        return 0
    if avg_this_round <= 0 or pay >= avg_this_round - 1e-9:
        return n
    f = normalize_low_wage_hire_formula(formula)
    if f == LOW_WAGE_HIRE_LINEAR:
        return int(n * (pay / avg_this_round))
    if f == LOW_WAGE_HIRE_AFFINE:
        c = 0.0 if affine_floor is None else float(affine_floor)
        return _affine_hire_effective(n, pay, avg_this_round, c)
    if f == LOW_WAGE_HIRE_UNIFIED_DYNAMIC:
        band, dfrac = _clamp_near_avg_blend_params(
            unified_dynamic_near_avg_band_rel,
            unified_dynamic_near_avg_dynamic_fraction,
        )
        if avg_this_round > 0 and pay > 0 and dfrac > 0:
            rel_gap = (avg_this_round - pay) / avg_this_round
            if rel_gap <= band:
                q = min(n, int(math.ceil(n * dfrac)))
                if q > 0:
                    easy = n - q
                    p_q = _field_unified_hire_params(
                        avg_this_round,
                        pay,
                        prev_for_role,
                        q,
                        avg_baseline=unified_field_avg_baseline,
                        salary_fluctuation=unified_field_salary_fluctuation,
                    )
                    return easy + _unified_lm_hire_effective(
                        q,
                        pay,
                        avg_this_round,
                        prev_for_role,
                        p_q,
                    )
        p = _field_unified_hire_params(
            avg_this_round,
            pay,
            prev_for_role,
            n,
            avg_baseline=unified_field_avg_baseline,
            salary_fluctuation=unified_field_salary_fluctuation,
        )
    else:
        p = _coerce_unified_params(unified_params)
    return _unified_lm_hire_effective(
        n, pay, avg_this_round, prev_for_role, p
    )


def compute_low_wage_quit(
    prev: int,
    pay: float,
    avg_this_round: float,
    new_delta_from_snapshot: int,
    formula: str,
    unified_params: Mapping[str, float],
    *,
    unified_field_avg_baseline: Optional[float] = None,
    unified_field_salary_fluctuation: Optional[float] = None,
) -> int:
    """
    低工资主动辞职人数（向下取整）。linear 或无工人时视为 0。
    new_delta_from_snapshot: 选手目标相对「本段 prev」的增员 max(0, 目标−prev)，与拟合脚本 npr 一致。
    """
    f = normalize_low_wage_hire_formula(formula)
    if not _unified_family(formula):
        return 0
    pr = max(0, int(prev))
    if pr <= 0:
        return 0
    if avg_this_round <= 0 or pay >= avg_this_round - 1e-9:
        return 0
    if f == LOW_WAGE_HIRE_UNIFIED_DYNAMIC:
        c0, c1, c2 = _field_unified_quit_coeffs(
            avg_this_round,
            pay,
            prev,
            new_delta_from_snapshot,
            avg_baseline=unified_field_avg_baseline,
            salary_fluctuation=unified_field_salary_fluctuation,
        )
    else:
        c0, c1, c2 = _coerce_quit_coeffs(unified_params)
    g = _gap(avg_this_round, pay)
    nd = max(0, int(new_delta_from_snapshot))
    npr = nd / pr
    inner = _sigmoid(c0 + c1 * g + c2 * npr)
    q = int(math.floor(pr * inner + 1e-9))
    return min(q, pr)
