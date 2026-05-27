# -*- coding: utf-8 -*-
"""
CPI / 销量结算模型枚举：与 admin 中「低工资公式」类似，由 CONFIG['cpi_sales_model'] 选择分支。
具体 Price/SPI/CPI 子公式复用 scripts.experimental_cpi_solo 中的实现，避免重复。
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def city_market_size_from_cfg(city_cfg: Optional[Mapping[str, Any]]) -> float:
    """场内市场规模：population × initial_penetration；缺任一则 0。"""
    if not city_cfg:
        return 0.0
    pop = city_cfg.get("population")
    pen = city_cfg.get("initial_penetration")
    if pop is None or pen is None:
        return 0.0
    try:
        return float(int(pop)) * float(pen)
    except (TypeError, ValueError):
        return 0.0


def compute_adaptive_k(avg_price_mean: float, market_size_total: float) -> Dict[str, float]:
    """
    Adaptive K formula: automatically scales K values to the economic environment
    defined by city avg_price and total market size (population × penetration).

    Tuned defaults (closer to official runtime observations):
    K_spi = avg_price × 0.002
    K_pqi = avg_price / 5000
    K_mi  = avg_price / 5    (management/person ≈ avg_price/5 gives MI ≈ 0.5)
    """
    ap = max(avg_price_mean, 1.0)
    return {
        "K_spi": max(1.0, ap * 0.002),
        "K_pqi": max(1.0, ap / 5000.0),
        "K_mi": max(1.0, ap / 5.0),
    }


# 多场景拟合默认锚点：(场均价 ref, 总市场规模 ref, K_spi 乘子, K_pqi 乘子, K_mi 乘子)
# 在 log(均价)×log(市场规模) 平面上对当前点做反距离加权混合，再乘到 compute_adaptive_k 的基准 K 上。
DEFAULT_ADAPTIVE_FITTED_ANCHORS: Tuple[Tuple[float, float, float, float, float], ...] = (
    (4500.0, 320_000.0, 1.08, 1.05, 0.96),
    (9800.0, 480_000.0, 1.0, 1.0, 1.0),
    (22000.0, 3_600_000.0, 0.90, 0.94, 1.06),
)


# ----- 纺锤形「模拟价格变化」：price_max_pct 下每轮合成均价 = product_price_max × 比例，比例按赛段在区间内随机 -----
# 赛段划分：按 (round-1)/(T-1) 三等分 → 初段 / 中段 / 末段（T=1 视为中段）
SPINDLE_BANDS: Dict[str, Dict[str, Optional[Tuple[float, float]]]] = {
    # 普通：初 0.8–0.9，中 0.9–0.95，末回到 0.8–0.9
    "normal": {
        "start": (0.8, 0.9),
        "middle": (0.9, 0.95),
        "end": (0.8, 0.9),
    },
    # 国赛：初 0.78–0.825，中 0.825–0.9，末 0.8–0.85
    "national": {
        "start": (0.78, 0.825),
        "middle": (0.825, 0.9),
        "end": (0.8, 0.85),
    },
    # 低价：初 0.66–0.715，中 0.725–0.775，末维持中段水平
    "low_price": {
        "start": (0.66, 0.715),
        "middle": (0.725, 0.775),
        "end": None,
    },
}


def normalize_spindle_profile(raw: Any) -> Optional[str]:
    """
    返回 canonical：normal / national / low_price；未启用或无法识别则 None。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    zh = {"普通": "normal", "国赛": "national", "低价": "low_price"}
    if s in zh:
        return zh[s]
    sl = s.lower()
    if sl in ("none", "off", "0", "false", "no"):
        return None
    if sl in ("normal", "national", "low_price"):
        return sl
    return None


def spindle_phase(round_index: int, total_rounds: int) -> str:
    """初段 start / 中段 middle / 末段 end（与 total_rounds 对齐）。"""
    r = max(1, int(round_index))
    T = max(1, int(total_rounds))
    if T <= 1:
        return "middle"
    t = (r - 1) / max(1, T - 1)
    if t < 1.0 / 3.0:
        return "start"
    if t < 2.0 / 3.0:
        return "middle"
    return "end"


def spindle_price_pct_band(profile: str, round_index: int, total_rounds: int) -> Tuple[float, float]:
    """
    当前轮在「纺锤」曲线下的比例随机区间 [lo, hi]（对 uniform 采样）。
    """
    p = normalize_spindle_profile(profile)
    if p is None:
        raise ValueError("spindle profile is required")
    if p not in SPINDLE_BANDS:
        raise ValueError(f"unknown spindle profile: {p!r}")
    ph = spindle_phase(round_index, total_rounds)
    row = SPINDLE_BANDS[p]
    band = row.get(ph)
    if band is None:
        band = row["middle"]
    lo, hi = float(band[0]), float(band[1])
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def spindle_price_pct_meta(config: Mapping[str, Any], state: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """结算 JSON 调试：当前轮纺锤赛段与比例区间（不随随机数变化）。"""
    prof = normalize_spindle_profile(config.get("cpi_price_spindle_profile"))
    if prof is None:
        return None
    r = int((state or {}).get("round") or 1)
    T = int(config.get("total_rounds") or 1)
    ph = spindle_phase(r, T)
    lo, hi = spindle_price_pct_band(prof, r, T)
    return {
        "profile": prof,
        "round": r,
        "total_rounds": T,
        "phase": ph,
        "pct_lo": round(lo, 6),
        "pct_hi": round(hi, 6),
    }


def adaptive_fitted_idw_weights(
    avg_price_mean: float,
    market_size_total: float,
    anchor_rows: Sequence[Tuple[float, float, float, float, float]],
) -> Tuple[str, Optional[int], List[float]]:
    """
    与 compute_adaptive_k_fitted 相同的对数距离 IDW 归一化权重（仅依赖锚点前两列坐标）。
    返回 (mode, exact_anchor_index 或 None, weights)；weights 长度等于锚点数且和为 1。
    mode: 'exact_anchor' | 'idw_log' | 'fallback_uniform'
    """
    rows = list(anchor_rows)
    if not rows:
        return ("fallback_uniform", None, [])

    ap = max(float(avg_price_mean), 1.0)
    ms = max(float(market_size_total), 1.0)
    lap = math.log(ap)
    lms = math.log(ms)

    for i, row in enumerate(rows):
        ap_i, ms_i = row[0], row[1]
        lap_i = math.log(max(ap_i, 1.0))
        lms_i = math.log(max(ms_i, 1.0))
        if (lap - lap_i) ** 2 + (lms - lms_i) ** 2 < 1e-18:
            return ("exact_anchor", i, [1.0 if j == i else 0.0 for j in range(len(rows))])

    weights: List[float] = []
    for ap_i, ms_i, _, _, _ in rows:
        lap_i = math.log(max(ap_i, 1.0))
        lms_i = math.log(max(ms_i, 1.0))
        d = (lap - lap_i) ** 2 + (lms - lms_i) ** 2
        weights.append(1.0 / (1e-12 + d))

    sw = sum(weights)
    if sw <= 0:
        return ("fallback_uniform", None, [1.0 / len(rows)] * len(rows))
    nw = [w / sw for w in weights]
    return ("idw_log", None, nw)


def coerce_adaptive_fitted_anchors(raw: Any) -> Optional[List[Tuple[float, float, float, float, float]]]:
    """
    可选 CONFIG['adaptive_fitted_anchors']：list of dict，键示例：
      avg_price, market_size, k_spi_mul, k_pqi_mul, k_mi_mul
    """
    if not isinstance(raw, list) or not raw:
        return None
    out: List[Tuple[float, float, float, float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            ap = float(item.get("avg_price") or item.get("ap") or 0)
            ms = float(item.get("market_size") or item.get("ms") or 0)
            if ap <= 0 or ms <= 0:
                continue
            m_spi = float(item.get("k_spi_mul") or item.get("mul_spi") or 1.0)
            m_pqi = float(item.get("k_pqi_mul") or item.get("mul_pqi") or 1.0)
            m_mi = float(item.get("k_mi_mul") or item.get("mul_mi") or 1.0)
            out.append((ap, ms, m_spi, m_pqi, m_mi))
        except (TypeError, ValueError):
            continue
    return out if out else None


def compute_adaptive_k_fitted(
    avg_price_mean: float,
    market_size_total: float,
    *,
    anchors: Optional[Sequence[Tuple[float, float, float, float, float]]] = None,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    多场景拟合版 adaptive：先算与原版相同的基准 K，再按 (均价, 总市场规模) 与若干锚点的对数距离
    做反比平方加权，混合各锚点上的 K 乘子，使不同 JR/区赛价格带与容量组合下曲线更连贯。

    market_size_total：参与销售城市的 population×penetration 之和（与 decision_submit 一致）。
    """
    base = compute_adaptive_k(avg_price_mean, market_size_total)
    ap = max(float(avg_price_mean), 1.0)
    ms = max(float(market_size_total), 1.0)

    rows = list(anchors) if anchors is not None else list(DEFAULT_ADAPTIVE_FITTED_ANCHORS)
    if not rows:
        rows = list(DEFAULT_ADAPTIVE_FITTED_ANCHORS)

    mode_w, exact_i, nw = adaptive_fitted_idw_weights(avg_price_mean, market_size_total, rows)
    if mode_w == "exact_anchor" and exact_i is not None:
        row = rows[exact_i]
        ms_spi, ms_pqi, ms_mi = row[2], row[3], row[4]
        meta = {
            "mode": "exact_anchor",
            "anchor_index": exact_i,
            "weights": nw,
            "multipliers": {"k_spi": ms_spi, "k_pqi": ms_pqi, "k_mi": ms_mi},
            "avg_price_mean": ap,
            "market_size_total": ms,
        }
        return (
            {
                "K_spi": max(1.0, base["K_spi"] * ms_spi),
                "K_pqi": max(1.0, base["K_pqi"] * ms_pqi),
                "K_mi": max(1.0, base["K_mi"] * ms_mi),
            },
            meta,
        )

    if mode_w == "fallback_uniform":
        meta = {
            "mode": "fallback_uniform",
            "weights": nw,
            "multipliers": {"k_spi": 1.0, "k_pqi": 1.0, "k_mi": 1.0},
            "avg_price_mean": ap,
            "market_size_total": ms,
        }
        return (dict(base), meta)

    ms_spi = sum(nw[i] * rows[i][2] for i in range(len(rows)))
    ms_pqi = sum(nw[i] * rows[i][3] for i in range(len(rows)))
    ms_mi = sum(nw[i] * rows[i][4] for i in range(len(rows)))
    meta = {
        "mode": "idw_log",
        "weights": [round(w, 6) for w in nw],
        "multipliers": {"k_spi": round(ms_spi, 6), "k_pqi": round(ms_pqi, 6), "k_mi": round(ms_mi, 6)},
        "avg_price_mean": ap,
        "market_size_total": ms,
        "anchor_count": len(rows),
    }
    return (
        {
            "K_spi": max(1.0, base["K_spi"] * ms_spi),
            "K_pqi": max(1.0, base["K_pqi"] * ms_pqi),
            "K_mi": max(1.0, base["K_mi"] * ms_mi),
        },
        meta,
    )


def synthetic_cpi_avg_from_price_max(
    config: Mapping[str, Any],
    *,
    state: Optional[Mapping[str, Any]] = None,
) -> Optional[float]:
    """
    单人训练：无多队表单时，用「合成市场均价」模拟多队价格带。
    当 cpi_price_target_mode == 'price_max_pct' 且 product_price_max > 0：
      均价 = product_price_max × 比例；比例默认每轮在 [min,max] 内随机（默认 0.75–0.85），
      若 cpi_price_target_max_pct > 0 则固定为该比例（优先于纺锤）。
    若 cpi_price_spindle_profile 已启用：比例在每轮所属赛段区间内随机（纺锤曲线，见 SPINDLE_BANDS）。
    该值同时用于 v3/adaptive 的 K 推导与 ratio_below_cap 的 price_target（全场各城同一scalar）。
    多队选手均价（cpi_use_cross_team_avg_price + avg_price_by_city）仍优先。
    """
    if str(config.get("cpi_price_target_mode") or "city_avg").strip().lower() != "price_max_pct":
        return None
    pmax = float(config.get("product_price_max") or 0)
    if pmax <= 0:
        return None
    fixed = float(config.get("cpi_price_target_max_pct") or 0)
    lo = float(config.get("cpi_price_target_max_pct_min") or 0.75)
    hi = float(config.get("cpi_price_target_max_pct_max") or 0.85)
    lo = max(0.01, min(1.0, lo))
    hi = max(lo, min(1.0, hi))
    if fixed > 0:
        frac = max(0.01, min(1.0, fixed))
    else:
        prof = normalize_spindle_profile(config.get("cpi_price_spindle_profile"))
        if prof is not None:
            r = int((state or {}).get("round") or 1)
            T = int(config.get("total_rounds") or 1)
            lo, hi = spindle_price_pct_band(prof, r, T)
            frac = random.uniform(lo, hi)
        else:
            frac = random.uniform(lo, hi)
    return pmax * frac


def resolve_cpi_sales_options(config: Mapping[str, Any]) -> Dict[str, Any]:
    """
    返回 decision_submit 使用的选项。model_id 写入结算 JSON 供报表核对。

    纺锤曲线：CONFIG['cpi_price_spindle_profile'] 与 price_max_pct 联用（见 synthetic_cpi_avg_from_price_max、SPINDLE_BANDS）。

    模型说明：
    - classic：与历史主程序完全一致（对称 Price 指数、线性 CPI、按 CPI 归一化城间权重、算术平均总因子）。
    - price_asymmetric：定价不高于城均价时 min(1, avg/price)，高于时同原高价惩罚。
    - geometric：CPI 各分项按同名权重做几何平均（乘积型）。
    - market_share：城间权重 ∝ 有效 CPI × 市场规模（pop×penetration）；总因子仍算术平均。
    - market_overall：城间权重仍按 CPI；总因子改为按市场规模加权平均各城有效 CPI。
    - market_full：market_share + market_overall 同时启用。
    - city_avg_spi：SPI 分母 K 取各城 Avg.Price 的算术平均（无则回退 market_report_price）。
    - adaptive：自适应 K 公式模型——K 由场内参数（avg_price、market_size）推导，无需手动调整即可
      跨赛制使用。多队模式时 avg_salary 取选手实际薪资均值，price_target 可选取选手均价。
    - adaptive_fitted：多场景拟合版 adaptive——在若干 (均价, 市场规模) 锚点上对基准 K 施加乘子，再按对数距离
      反比加权混合；可选 CONFIG['adaptive_fitted_anchors'] 覆盖默认锚点。
    - CONFIG['cpi_use_cross_team_avg_price']=True 时，v3/adaptive 等也会在 resolve 之后合并进 opts，
      使 decision_submit 用 cross_team_overrides['avg_price_by_city']（见 1.py _compute_cross_team_overrides）。
    - v3：ratio_below_cap + 自定义权重；K 由场均价推导（见 decision_submit 中 v3 分支），可用 admin cpi_k_* 覆盖。
    - market_competitive_v4：多队同场时按城用 V4 CPI（测试表 baseline_best_fit + 可选测试表合成队）分市场份额，再按各队可售库存缩放；需配合多队结算预计算。
    - v4m：多队同场时按官方可见变量 Price/SPI/PQI/MI 的相对份额公式分市场份额，再按各队可售库存缩放；实验模型。
    """
    raw = str(config.get("cpi_sales_model") or "classic").strip().lower()
    aliases = {
        "default": "classic",
        "official": "classic",
        "linear": "classic",
        "price_ratio": "price_asymmetric",
        "price_ratio_below": "price_asymmetric",
        "geo": "geometric",
        "demand_proxy": "market_share",
        "spi_city_avg": "city_avg_spi",
        "auto": "adaptive",
        "adaptive_fit": "adaptive_fitted",
        "adaptive_multi": "adaptive_fitted",
        "adaptive_v2": "adaptive_v2",
        "adaptive-v2": "adaptive_v2",
        "v4_multi": "market_competitive_v4",
        "market_v4": "market_competitive_v4",
        "v4_m": "v4m",
        "fun": "have_fun",
        "solo_floor": "have_fun",
        "v5_m": "v5m",
        "v5_pool": "v5p",
        "pool": "v5p",
        "v6_m": "v6m",
        "city_slice_pool": "v6m",
        "slice_pool": "v6m",
        "official_multi": "v4m",
        "multi_relative": "v4m",
    }
    m = aliases.get(raw, raw)

    base = {
        "model_id": m,
        "price_index_mode": "symmetric",
        "cpi_combine": "linear",
        "share_weighting": "cpi",
        "use_mean_city_avg_k_spi": False,
        "overall_cpi_mode": "arithmetic_mean",
    }

    if m == "classic":
        return base

    if m == "price_asymmetric":
        out = dict(base)
        out["price_index_mode"] = "ratio_below_cap"
        out["model_id"] = "price_asymmetric"
        return out

    if m == "geometric":
        out = dict(base)
        out["cpi_combine"] = "geometric"
        out["model_id"] = "geometric"
        return out

    if m == "market_share":
        out = dict(base)
        out["share_weighting"] = "demand_proxy"
        out["model_id"] = "market_share"
        return out

    if m == "market_overall":
        out = dict(base)
        out["overall_cpi_mode"] = "market_size_weighted_mean"
        out["model_id"] = "market_overall"
        return out

    if m == "market_full":
        out = dict(base)
        out["share_weighting"] = "demand_proxy"
        out["overall_cpi_mode"] = "market_size_weighted_mean"
        out["model_id"] = "market_full"
        return out

    if m == "city_avg_spi":
        out = dict(base)
        out["use_mean_city_avg_k_spi"] = True
        out["model_id"] = "city_avg_spi"
        return out

    if m == "adaptive":
        out = dict(base)
        out["model_id"] = "adaptive"
        out["k_formula"] = "adaptive"
        out["use_cross_team_avg_salary"] = False
        out["use_cross_team_avg_price"] = False
        out["price_index_mode"] = "ratio_below_cap"
        # 与 v3 同口径权重，降低 price 主导，提升 SPI/MI 对高投入策略的区分度
        out["cpi_weights"] = {
            "price": 0.20,
            "spi": 0.35,
            "pqi": 0.15,
            "mi": 0.30,
        }
        out["cpi_weights_no_mi"] = {
            "price": 0.40,
            "spi": 0.30,
            "pqi": 0.30,
        }
        return out

    if m == "adaptive_v2":
        out = dict(base)
        out["model_id"] = "adaptive_v2"
        out["k_formula"] = "adaptive"
        out["use_cross_team_avg_salary"] = False
        out["use_cross_team_avg_price"] = False
        out["price_index_mode"] = "ratio_below_cap"
        out["overall_cpi_mode"] = "max_city_cpi"
        out["cpi_weights"] = {
            "price": 0.20,
            "spi": 0.35,
            "pqi": 0.15,
            "mi": 0.30,
        }
        out["cpi_weights_no_mi"] = {
            "price": 0.40,
            "spi": 0.30,
            "pqi": 0.30,
        }
        return out

    if m == "adaptive_fitted":
        out = dict(base)
        out["model_id"] = "adaptive_fitted"
        out["k_formula"] = "adaptive_fitted"
        out["use_cross_team_avg_salary"] = False
        out["use_cross_team_avg_price"] = False
        out["price_index_mode"] = "ratio_below_cap"
        out["cpi_weights"] = {
            "price": 0.20,
            "spi": 0.35,
            "pqi": 0.15,
            "mi": 0.30,
        }
        out["cpi_weights_no_mi"] = {
            "price": 0.40,
            "spi": 0.30,
            "pqi": 0.30,
        }
        return out

    if m == "v3":
        out = dict(base)
        out["model_id"] = "v3"
        out["k_formula"] = "v3"
        out["price_index_mode"] = "ratio_below_cap"
        out["cpi_weights"] = {
            "price": 0.20,
            "spi": 0.35,
            "pqi": 0.15,
            "mi": 0.30,
        }
        out["cpi_weights_no_mi"] = {
            "price": 0.40,
            "spi": 0.30,
            "pqi": 0.30,
        }
        return out

    if m == "market_competitive_v4":
        out = dict(base)
        out["model_id"] = "market_competitive_v4"
        return out

    if m == "v4m":
        out = dict(base)
        out["model_id"] = "v4m"
        return out

    if m == "have_fun":
        out = dict(base)
        out["model_id"] = "have_fun"
        return out

    if m == "v5m":
        out = dict(base)
        out["model_id"] = "v5m"
        return out

    if m == "v5p":
        out = dict(base)
        out["model_id"] = "v5p"
        return out

    if m == "v6m":
        out = dict(base)
        out["model_id"] = "v6m"
        return out

    # 未知值回退 classic，避免结算异常
    out = dict(base)
    out["model_id"] = "classic"
    out["_unknown_cpi_sales_model"] = raw
    return out
