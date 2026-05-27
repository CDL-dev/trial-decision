# -*- coding: utf-8 -*-
"""
实验性「单人模式」CPI 模型：从 decision_submit.py 销售段抽离的纯函数实现，
不依赖 Flask / GAME_STATE，便于扫参、对照报表与做假设实验。

逆向/对照时的铁律（与场内一致）：
- 只使用「当场内可得到」的输入：本方表单、当轮结算/报表 JSON、admin CONFIG 里已生效的
  城市表（avg_price、population、penetration 等）、以及当轮已抽定的市场平均工资
 （与 hr_round / 结算里 avg_worker_salary_this_round、avg_engineer_salary_this_round 一致）。
- 不引入仅为拟合观测而增加的任意参数；本文件中的公式项与 decision_submit 一一对应，
  无额外拟合自由度。
- 注意：SoloCPIConfig 里的默认 avg_worker_salary / avg_engineer_salary 仅便于跑 demo；
  复现某轮真实 CPI 时必须换成该轮 JSON 中的当值，否则相当于用场外常数代替场内随机结果。
- online_market_multiplier：仅当比赛 GAME_STATE 中对该城有系数时使用；默认 1.0 表示未启用
  在线乘子，不可为凑销量随意改。

对齐逻辑见 decision_submit.py「销售相关：CPI（Price/SPI/PQI/MI）→ 影响销量」
（约 L500–L644）：公式与权重一致；market_cap 与主程序人口×渗透率一致，须来自同一配置。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple


@dataclass
class CitySoloInput:
    """单城决策输入（与 sales_data 单行 + cities_config 中该城 avg_price 对应）。"""

    name: str
    price: float
    marketing: float
    agents: int
    avg_price: float = 0.0
    online_market_multiplier: float = 1.0
    # 场内市场规模（population×penetration 等）；用于实验性城间权重，与报表 Market size 列一致
    market_size: float = 0.0


@dataclass
class SoloCPIConfig:
    """与 CONFIG 中 CPI 相关键对应；缺省贴近 1.py 常见值。"""

    pqi_old_product_weight: float = 1.0
    market_report_price: float = 200000.0
    has_management_mechanism: bool = True
    avg_worker_salary: float = 3000.0
    avg_engineer_salary: float = 5000.0
    # True：PQI/MI 的 K 仅用 avg_engineer_salary（无工人产线，与 decision_submit 一致）
    pqi_mi_k_engineer_only: bool = False
    # True：K_spi 用本轮各城 Avg.Price 算术平均（仅实验/报表对照，场内可从市场表读取）
    use_mean_city_avg_price_as_k_spi: bool = False


@dataclass
class SoloRoundProduction:
    """影响 PQI 分母的全局量（与当轮 quality_investment、库存与产量一致）。"""

    quality_investment: float = 0.0
    products_inventory_before: int = 0
    products_produced: int = 0


def _k_pqi_mi(cfg: SoloCPIConfig) -> float:
    if getattr(cfg, "pqi_mi_k_engineer_only", False):
        return max(1.0, float(cfg.avg_engineer_salary))
    return max(
        1.0,
        (float(cfg.avg_worker_salary) + float(cfg.avg_engineer_salary)) / 2.0,
    )


def compute_pqi_index(
    prod: SoloRoundProduction,
    cfg: SoloCPIConfig,
) -> Tuple[float, float]:
    """
    pqi_raw = quality_invest / (old_products * X + new_products)
    pqi_index = pqi_raw / (pqi_raw + K_pqi)，K_pqi = K_pqi_mi
    """
    x = float(cfg.pqi_old_product_weight or 1.0)
    denom = prod.products_inventory_before * x + max(int(prod.products_produced), 0)
    pqi_raw = float(prod.quality_investment) / denom if denom > 0 else 0.0
    k = _k_pqi_mi(cfg)
    pqi_index = pqi_raw / (pqi_raw + k) if pqi_raw > 0 and k > 0 else 0.0
    return pqi_raw, pqi_index


def compute_mi_index(
    management_investment: float,
    total_people: int,
    cfg: SoloCPIConfig,
) -> Tuple[float, float]:
    """mi_raw = management_index = invest / (workers+engineers)；与游戏内一致。"""
    if not cfg.has_management_mechanism or total_people <= 0:
        return 0.0, 0.0
    mi_raw = float(management_investment) / float(total_people)
    k_mi = _k_pqi_mi(cfg)
    mi_index = mi_raw / (mi_raw + k_mi) if mi_raw > 0 and k_mi > 0 else 0.0
    return mi_raw, mi_index


def price_index_city(price: float, price_target: float) -> float:
    """Price 指数：越接近该城基准价越高，clip 到 [0,1] 语义（仅下界）。"""
    return price_index_city_mode(price, price_target, "symmetric")


def price_index_city_mode(price: float, price_target: float, mode: str) -> float:
    """
    symmetric：与 decision_submit 一致，1 - |p-t|/t。
    ratio_below_cap：仅由均价与定价推导——不引入新常数；定价不高于均价时 min(1, t/p)，
    高于均价时 1 - (p-t)/t（与高价侧惩罚连续）。
    """
    if price_target <= 0 or price <= 0:
        return 0.0
    m = (mode or "symmetric").strip().lower()
    if m == "ratio_below_cap":
        if price <= price_target:
            return min(1.0, price_target / price)
        return max(0.0, 1.0 - (price - price_target) / price_target)
    return max(0.0, 1.0 - abs(price - price_target) / price_target)


def spi_index_city(
    marketing: float,
    agents: int,
    k_spi: float,
) -> float:
    """SPI：spi_raw = Marketing * (1 + 0.10 * agents)；再除以 (spi_raw + K_spi)。"""
    spi_raw = float(marketing) * (1.0 + 0.10 * int(agents))
    k = k_spi if k_spi > 0 else 1.0
    return spi_raw / (spi_raw + k) if spi_raw > 0 and k > 0 else 0.0


def cpi_index_city(
    price_idx: float,
    spi_idx: float,
    pqi_idx: float,
    mi_idx: float,
    has_management: bool,
) -> float:
    """有 MI：40/20/20/20；无 MI：40/30/30。"""
    return cpi_index_city_mode(
        price_idx, spi_idx, pqi_idx, mi_idx, has_management, "linear"
    )


def cpi_index_city_mode(
    price_idx: float,
    spi_idx: float,
    pqi_idx: float,
    mi_idx: float,
    has_management: bool,
    combine: str,
    *,
    w_price: Optional[float] = None,
    w_spi: Optional[float] = None,
    w_pqi: Optional[float] = None,
    w_mi: Optional[float] = None,
) -> float:
    """
    linear：与主程序一致。
    geometric：∏ max(idx,ε)^w（文档 5.1 乘积型猜想）。
    可选自定义权重：w_price / w_spi / w_pqi / w_mi。
    未传时回退默认（有 MI: 40/20/20/20；无 MI: 40/30/30）。
    """
    c = (combine or "linear").strip().lower()
    eps = 1e-9
    if has_management:
        _w_price = 0.4 if w_price is None else float(w_price)
        _w_spi = 0.2 if w_spi is None else float(w_spi)
        _w_pqi = 0.2 if w_pqi is None else float(w_pqi)
        _w_mi = 0.2 if w_mi is None else float(w_mi)
    else:
        _w_price = 0.4 if w_price is None else float(w_price)
        _w_spi = 0.3 if w_spi is None else float(w_spi)
        _w_pqi = 0.3 if w_pqi is None else float(w_pqi)
    if has_management:
        if c == "geometric":
            return (
                max(price_idx, eps) ** _w_price
                * max(spi_idx, eps) ** _w_spi
                * max(pqi_idx, eps) ** _w_pqi
                * max(mi_idx, eps) ** _w_mi
            )
        return (
            _w_price * price_idx
            + _w_spi * spi_idx
            + _w_pqi * pqi_idx
            + _w_mi * mi_idx
        )
    if c == "geometric":
        return (
            max(price_idx, eps) ** _w_price
            * max(spi_idx, eps) ** _w_spi
            * max(pqi_idx, eps) ** _w_pqi
        )
    return _w_price * price_idx + _w_spi * spi_idx + _w_pqi * pqi_idx


def compute_cpi_pipeline(
    cities: List[CitySoloInput],
    prod: SoloRoundProduction,
    cfg: SoloCPIConfig,
    *,
    management_investment: float = 0.0,
    total_people: int = 0,
    fallback_price_target: Optional[float] = None,
    pqi_index_override: Optional[float] = None,
    pqi_raw_override: Optional[float] = None,
    price_index_mode: str = "symmetric",
    cpi_combine: str = "linear",
    share_weighting: str = "cpi",
    overall_cpi_mode: str = "arithmetic_mean",
) -> Dict[str, object]:
    """
    完整跑一遍：PQI/MI → 逐城 Price/SPI/CPI → 有效 CPI（含无 Agent 置 0）→
    market_share、effective_sales_factor、按占比的加权 price/spi（与主程序标量一致）。

    pqi_index_override：若给定则跳过 prod 推 PQI，直接用该指数（如表内已算好的 PQI）。
    pqi_raw_override：若给定则用 pqi_index = raw/(raw+K_pqi)，与 pqi_index_override 互斥。

    以下为「仅实验/对照」可选分支（默认与 decision_submit 一致）：
    price_index_mode：symmetric | ratio_below_cap
    cpi_combine：linear | geometric
    share_weighting：cpi | demand_proxy（城间权重 ∝ 有效CPI×market_size，见文档 demand∝market_size×CPI）
    overall_cpi_mode：arithmetic_mean（与主程序一致，sum_eff/城数）| market_size_weighted_mean
        （各城有效CPI 按 market_size 加权平均，仅当各城 market_size>0 时启用）
    """
    if pqi_index_override is not None:
        pqi_raw = None
        pqi_index = float(pqi_index_override)
    elif pqi_raw_override is not None:
        k = _k_pqi_mi(cfg)
        r = max(0.0, float(pqi_raw_override))
        pqi_raw = r
        pqi_index = r / (r + k) if r > 0 and k > 0 else 0.0
    else:
        pqi_raw, pqi_index = compute_pqi_index(prod, cfg)
    mi_raw, mi_index = compute_mi_index(
        management_investment, total_people, cfg
    )

    k_spi = float(cfg.market_report_price or 0.0)
    if getattr(cfg, "use_mean_city_avg_price_as_k_spi", False):
        avs = [
            float(c.avg_price)
            for c in cities
            if float(getattr(c, "avg_price", 0) or 0) > 0
        ]
        if avs:
            k_spi = sum(avs) / float(len(avs))
    if k_spi <= 0:
        k_spi = 1.0

    fb = (
        float(fallback_price_target)
        if fallback_price_target is not None
        else float(cfg.market_report_price or 0.0)
    )

    price_by_city: Dict[str, float] = {}
    spi_by_city: Dict[str, float] = {}
    cpi_by_city: Dict[str, float] = {}
    effective_by_city: Dict[str, float] = {}

    for c in cities:
        target = float(c.avg_price or 0.0)
        if target <= 0:
            target = fb
        p_idx = price_index_city_mode(
            float(c.price), target, price_index_mode
        )
        s_idx = spi_index_city(c.marketing, c.agents, k_spi)
        c_idx = cpi_index_city_mode(
            p_idx,
            s_idx,
            pqi_index,
            mi_index,
            cfg.has_management_mechanism,
            cpi_combine,
        )
        mult = float(c.online_market_multiplier or 1.0)
        eff = max(0.0, min(1.0, c_idx * mult))
        price_by_city[c.name] = p_idx
        spi_by_city[c.name] = s_idx
        cpi_by_city[c.name] = c_idx
        effective_by_city[c.name] = eff

    for c in cities:
        if int(c.agents) < 1:
            effective_by_city[c.name] = 0.0

    city_names = [c.name for c in cities]
    sum_eff = sum(effective_by_city[c] for c in city_names)
    n_active = sum(1 for c in cities if int(getattr(c, "agents", 0) or 0) >= 1)

    sw = (share_weighting or "cpi").strip().lower()
    if sum_eff > 0:
        if sw == "demand_proxy":
            raw_w: Dict[str, float] = {}
            for c in cities:
                e = effective_by_city[c.name]
                ms = float(c.market_size or 0.0)
                raw_w[c.name] = e * (ms if ms > 0.0 else 1.0)
            s_w = sum(raw_w.values())
            if s_w > 0:
                share = {name: raw_w[name] / s_w for name in city_names}
            else:
                share = {c: effective_by_city[c] / sum_eff for c in city_names}
        else:
            share = {c: effective_by_city[c] / sum_eff for c in city_names}
        omode = (overall_cpi_mode or "arithmetic_mean").strip().lower()
        if omode == "market_size_weighted_mean":
            sum_ms_act = sum(
                float(getattr(c, "market_size", 0) or 0)
                for c in cities
                if int(getattr(c, "agents", 0) or 0) >= 1
            )
            num_ms = sum(
                effective_by_city[c.name] * float(getattr(c, "market_size", 0) or 0)
                for c in cities
                if int(getattr(c, "agents", 0) or 0) >= 1
            )
            if sum_ms_act > 0:
                overall = num_ms / sum_ms_act
            else:
                overall = sum_eff / max(1.0, float(n_active))
        else:
            overall = sum_eff / max(1.0, float(n_active))
        effective_sales_factor = max(0.0, min(1.0, overall))
    else:
        share = {c: 0.0 for c in city_names}
        effective_sales_factor = 0.0

    price_scalar = sum(
        price_by_city[c] * share.get(c, 0.0) for c in city_names
    )
    spi_scalar = sum(spi_by_city[c] * share.get(c, 0.0) for c in city_names)

    return {
        "pqi_raw": pqi_raw,
        "pqi_index": pqi_index,
        "mi_raw": mi_raw,
        "mi_index": mi_index,
        "price_index_mode": price_index_mode,
        "cpi_combine": cpi_combine,
        "share_weighting": share_weighting,
        "overall_cpi_mode": overall_cpi_mode,
        "use_mean_city_avg_price_as_k_spi": bool(
            getattr(cfg, "use_mean_city_avg_price_as_k_spi", False)
        ),
        "K_pqi_mi": _k_pqi_mi(cfg),
        "K_spi": k_spi,
        "price_index_by_city": price_by_city,
        "spi_index_by_city": spi_by_city,
        "cpi_index_by_city": cpi_by_city,
        "cpi_effective_by_city": effective_by_city,
        "market_share_by_city": share,
        "effective_sales_factor": effective_sales_factor,
        "cpi_index_scalar": effective_sales_factor,
        "price_index_scalar": price_scalar,
        "spi_index_scalar": spi_scalar,
    }


def compute_products_sold_total(
    available_products: int,
    effective_sales_factor: float,
    *,
    market_cap_by_cities: Optional[int] = None,
) -> int:
    """
    与 decision_submit 一致：sold_total = round(available * factor)，
    再与 market_cap、available 取 min/max。
    """
    ap = max(0, int(available_products))
    f = max(0.0, min(1.0, float(effective_sales_factor)))
    total = int(round(ap * f))
    if market_cap_by_cities is not None and market_cap_by_cities > 0:
        total = min(total, int(market_cap_by_cities))
    return max(0, min(total, ap))


def allocate_integer_sales(
    products_sold_total: int,
    market_share: Mapping[str, float],
    city_order: List[str],
) -> Dict[str, int]:
    """
    与主程序相同：在已定总销量 products_sold_total 下，
    按 market_share 做 floor + 最大余数分配整数到各城。
    """
    total = max(0, int(products_sold_total))

    sold: Dict[str, int] = {}
    fractions: List[Tuple[float, str]] = []
    floor_sum = 0
    for city in city_order:
        raw = total * float(market_share.get(city, 0.0))
        fl = int(raw)
        sold[city] = fl
        floor_sum += fl
        fractions.append((raw - fl, city))
    remaining = total - floor_sum
    fractions.sort(key=lambda x: x[0], reverse=True)
    i = 0
    while remaining > 0 and i < len(fractions):
        _, c = fractions[i]
        sold[c] = sold.get(c, 0) + 1
        remaining -= 1
        i += 1
    return sold


def _demo() -> None:
    cfg = SoloCPIConfig()
    cities = [
        CitySoloInput("CityA", price=120.0, marketing=500_000.0, agents=2, avg_price=118.0),
        CitySoloInput("CityB", price=95.0, marketing=300_000.0, agents=1, avg_price=100.0),
    ]
    prod = SoloRoundProduction(
        quality_investment=400_000.0,
        products_inventory_before=50,
        products_produced=30,
    )
    out = compute_cpi_pipeline(
        cities,
        prod,
        cfg,
        management_investment=600_000.0,
        total_people=20,
    )
    print("=== experimental_cpi_solo demo ===")
    for k in (
        "pqi_index",
        "mi_index",
        "effective_sales_factor",
        "cpi_index_scalar",
        "market_share_by_city",
        "cpi_effective_by_city",
    ):
        print(f"{k}: {out[k]}")
    ap = 80
    st = compute_products_sold_total(ap, float(out["effective_sales_factor"]))
    sold = allocate_integer_sales(
        st,
        out["market_share_by_city"],
        [c.name for c in cities],
    )
    print("products_sold_total:", st, "(from available", ap, "* factor)")
    print("sold_by_city:", sold, "sum", sum(sold.values()))


if __name__ == "__main__":
    _demo()
