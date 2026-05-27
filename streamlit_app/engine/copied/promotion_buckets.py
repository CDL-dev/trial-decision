# -*- coding: utf-8 -*-
"""
雇佣后滞后若干「决策轮」再计入晋升产能档（promoted）：流水线分桶，无需按轮存历史 JSON。

- 每轮开始时：最前一档并入 promoted，其余档前移，末档置 0（新录用在本轮末写入末档）。
- 辞职：优先从 promoted 扣（对齐 Report 侧「Quitted 优先晋升员工」的总量近似）。
- 为达到目标在岗而额外离开的人：优先从末档学徒向前扣（近似「先裁新聘」）。
"""
from __future__ import annotations

from typing import List, MutableSequence, Tuple

ROLE_PREFIX = {"worker": "worker", "engineer": "engineer"}


def clamp_promotion_lag(raw) -> int:
    try:
        L = int(raw)
    except (TypeError, ValueError):
        L = 2
    return max(1, min(6, L))


def _keys(role: str) -> Tuple[str, str]:
    p = ROLE_PREFIX.get(role, role)
    return f"{p}_promoted", f"{p}_junior_stages"


def promotion_migrate_if_needed(
    state: dict, role: str, lag: int, prev_headcount: int
) -> None:
    """旧存档无分桶字段时：将全部在岗算作已晋升，避免突然降产能。"""
    k_s, k_j = _keys(role)
    if k_j in state and isinstance(state[k_j], list) and len(state[k_j]) == lag:
        return
    state[k_s] = max(0, int(prev_headcount))
    state[k_j] = [0] * lag


def promotion_tick(state: dict, role: str, lag: int) -> None:
    """每轮 HR 前调用：学徒最前一档并入 promoted 并整体前移。"""
    k_s, k_j = _keys(role)
    st: List[int] = [max(0, int(x)) for x in (state.get(k_j) or [0] * lag)]
    while len(st) < lag:
        st.append(0)
    st = st[:lag]
    S = max(0, int(state.get(k_s) or 0))
    S += st[0]
    for i in range(lag - 1):
        st[i] = st[i + 1]
    if lag > 0:
        st[lag - 1] = 0
    state[k_s] = S
    state[k_j] = st


def _rebalance_to_prev(S: int, st: List[int], target: int) -> Tuple[int, List[int]]:
    """使 S+sum(st)==target（信任 prev 标量）。"""
    S = max(0, int(S))
    st = [max(0, int(x)) for x in st]
    diff = int(target) - (S + sum(st))
    if diff == 0:
        return S, st
    st = list(st)
    if diff > 0:
        if st:
            st[-1] += diff
        else:
            S += diff
        return S, st
    d = -diff
    i = len(st) - 1
    while d > 0 and i >= 0:
        take = min(st[i], d)
        st[i] -= take
        d -= take
        i -= 1
    while d > 0:
        take = min(S, d)
        S -= take
        d -= take
    return S, st


def _remove_n(
    buckets: MutableSequence[int], n: int, indices: List[int]
) -> None:
    remain = int(n)
    for idx in indices:
        if remain <= 0:
            break
        take = min(buckets[idx], remain)
        buckets[idx] -= take
        remain -= take
    if remain > 0:
        for idx in reversed(range(len(buckets))):
            if remain <= 0:
                break
            take = min(buckets[idx], remain)
            buckets[idx] -= take
            remain -= take


def promotion_apply_hr(
    state: dict,
    role: str,
    lag: int,
    pw0: int,
    pw1: int,
    quit_n: int,
    final_total: int,
    new_hire_n: int,
    *,
    enabled: bool,
) -> None:
    """
    pw0: 本轮 HR 前 prev_*（已与 tick 后分桶之和一致）
    pw1: 辞职后、招聘前在岗
    quit_n: 本轮回合辞职人数
    final_total: 本轮末 effective 总人数
    new_hire_n: 实际新招人数
    """
    k_s, k_j = _keys(role)
    if not enabled:
        # 关闭工人机制时仍用 prev_workers / 产能逻辑算人数，不能把分桶清零（否则与 prev_workers 脱节、
        # 报表 tick 后快照全 0，且下轮 migrate_if_needed 因长度已匹配不会重建）。
        ft = max(0, int(final_total))
        state[k_s] = ft
        state[k_j] = [0] * lag
        return

    S = max(0, int(state.get(k_s) or 0))
    st: List[int] = [max(0, int(x)) for x in (state.get(k_j) or [0] * lag)]
    while len(st) < lag:
        st.append(0)
    st = st[:lag]

    S, st = _rebalance_to_prev(S, st, int(pw0))

    buckets: List[int] = [S] + st
    quit_n = max(0, int(quit_n))
    # 辞职：先扣 promoted，再依次扣各学徒档
    quit_order = list(range(len(buckets)))
    _remove_n(buckets, quit_n, quit_order)

    if sum(buckets) != int(pw1):
        # 数值漂移时以 pw1 为准收束到末档
        d = int(pw1) - sum(buckets)
        buckets[-1] = max(0, buckets[-1] + d)

    carried = int(final_total) - max(0, int(new_hire_n))
    extra = sum(buckets) - carried
    if extra > 0:
        layoff_order = list(reversed(range(len(buckets))))
        _remove_n(buckets, extra, layoff_order)

    if lag > 0:
        buckets[-1] = max(0, buckets[-1] + max(0, int(new_hire_n)))

    S_out, st_out = buckets[0], buckets[1:]
    if S_out + sum(st_out) != int(final_total):
        d = int(final_total) - (S_out + sum(st_out))
        if lag > 0:
            st_out = list(st_out)
            st_out[-1] = max(0, st_out[-1] + d)
        else:
            S_out = max(0, S_out + d)

    state[k_s] = max(0, S_out)
    state[k_j] = [max(0, int(x)) for x in st_out]


def junior_sum(state: dict, role: str) -> int:
    _, k_j = _keys(role)
    st = state.get(k_j) or []
    return sum(max(0, int(x)) for x in st)
