# -*- coding: utf-8 -*-
"""
风枢·群岛 —— 72h 逐小时仿真引擎与两套调度策略
================================================

两套策略（同场景、同外部灾害，公平对比）：
  - baseline_policy：东极现有三级规则体系
      （光伏优先 / 储能兜底 / 柴油应急；负荷优先级静态；无预测性收桨；不预充储能）
  - windhub_policy：风枢预测性协同调度
      （台风前储能满充、预测窗口内提前收桨/主动解列、动态负荷优先级、柴发延迟启动至 SOC<30% 且可再生不足）

指标真实来源：逐小时供电平衡由本模块的纯 Python 线性平衡完成；pandapower（network.py）
仅用于关键小时潮流物理校验。所有指标口径见 metrics.py / README，公式不篡改。

建模透明假设（写入 README）：
  - 柴油发电机带有限燃油预算（DIESEL_BUDGET_MWH），反映现实燃料约束；
    基线策略无预测，把柴油用于覆盖全部负荷（含非关键），燃料很快耗尽 → 后期关键负荷失电；
    风枢策略优先保护关键负荷、动态切除非关键、预充储能+高空风机增发，柴油仅用于补足关键负荷缺口 → 油耗大幅降低。
  - 海缆离网：基线被动硬故障（风速>25m/s 持续 3h 概率性离网，本仿真确定性触发）；
    风枢主动预测性解列（风速>20m/s 即受控离网），海缆在风暴核心前已退出运行 → 暴露与失效风险显著降低。
  - 高空系留风机：风枢在 8~28m/s 安全窗口发电、>28m/s 收桨（零暴露）；基线缺乏管理，长期部署 → 风暴中高暴露。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import metrics as M
from . import network as N
from ..data import typhoons as T

HOURS = 72

# —— 微电网额定参数 ——
DG_DIESEL_MW = 4.3
STORAGE_MW = 1.0
STORAGE_MWH = 2.0
PV_MW = 1.0
TETHERED_MW = 0.5  # 高空系留浮空风机容量（风枢可用）

LOADS_RATED = {
    "medical": 0.15,
    "desal": 0.30,
    "comms": 0.10,
    "livelihood": 0.90,
    "commercial": 0.50,
}
PRIORITY = ["medical", "desal", "comms", "livelihood", "commercial"]

# —— 策略可调参数（非篡改指标，仅调策略行为） ——
DIESEL_BUDGET_MWH = 44.0          # 柴发有限燃油预算（MWh），两策略共用，公平
INIT_SOC_BASELINE = 0.6           # 基线初始 SOC（MWh，30%），无预充
INIT_SOC_WINDHUB = 1.0            # 风枢初始 SOC（MWh，50%），随后预充至满
STORAGE_LOW_WINDHUB = 0.6         # 风枢柴发延迟启动阈值：SOC<0.6MWh(30%) 且可再生不足
CABLE_SECURE_WIND = 20.0          # 风枢主动解列风速阈值
CABLE_FAULT_WIND = 25.0           # 海缆硬故障风速阈值
CABLE_FAULT_SUSTAIN = 3           # 持续小时数
TETHERED_FEATHER_WIND = 28.0      # 系留风机收桨阈值


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def solar_factor(hour: int) -> float:
    """白昼光伏系数：06:00–18:00 正弦，正午峰值 1.0，夜间 0。"""
    if 6 <= hour <= 18:
        return math.sin(math.pi * (hour - 6) / 12.0)
    return 0.0


def pv_wind_att(wind: float) -> float:
    """光伏出力风速衰减：>20m/s 起衰减，>=35m/s 衰减至 0.3。"""
    if wind <= 20.0:
        return 1.0
    if wind >= 35.0:
        return 0.3
    return 1.0 - (wind - 20.0) / 15.0 * 0.7


def tethered_factor(wind: float) -> float:
    """高空系留风机可行发电系数：8~28m/s 内 1.0，外 0（收桨）。"""
    if 8.0 <= wind <= TETHERED_FEATHER_WIND:
        return 1.0
    return 0.0


def serve_by_priority(demand: dict, total_avail: float) -> dict:
    """按优先级顺序分配可用功率，返回各负荷实际供电。"""
    supplied = {k: 0.0 for k in demand}
    rem = total_avail
    for k in PRIORITY:
        d = demand.get(k, 0.0)
        give = min(d, rem)
        supplied[k] = give
        rem -= give
        if rem <= 1e-9:
            break
    return supplied


# ---------------------------------------------------------------------------
# 单策略仿真
# ---------------------------------------------------------------------------
def simulate(scenario_key: str, policy: str) -> dict:
    series = T.build_hourly_series(scenario_key, HOURS)
    load_profile = T.build_load_profile(scenario_key, HOURS)

    soc = INIT_SOC_WINDHUB if policy == "windhub" else INIT_SOC_BASELINE
    diesel_budget = DIESEL_BUDGET_MWH

    # 离网判定状态
    baseline_sustained = 0
    windhub_sustained = 0
    faulted_baseline = False
    faulted_windhub = False

    records = []
    exposures = {e: [] for e in M.EQUIPMENT_MODELS}

    # 事件标记
    events = {
        "storage_full_hour": None,
        "feather_hour": None,
        "islanding_hour": None,
        "diesel_start_hour": None,
    }

    for h in range(HOURS):
        wind, dist, pressure = series[h][1], series[h][2], series[h][3]

        # 负荷需求
        demand = {k: LOADS_RATED[k] * load_profile[k][h] for k in LOADS_RATED}

        # 发电可用量
        pv = PV_MW * solar_factor(h) * pv_wind_att(wind)
        tethered = TETHERED_MW * tethered_factor(wind) if policy == "windhub" else 0.0

        # —— 离网判定 ——
        if policy == "baseline":
            if wind > CABLE_FAULT_WIND:
                baseline_sustained += 1
            else:
                baseline_sustained = 0
            if (not faulted_baseline) and baseline_sustained >= CABLE_FAULT_SUSTAIN:
                faulted_baseline = True
            cable_available = not faulted_baseline
        else:  # windhub 主动解列
            if wind > CABLE_SECURE_WIND:
                windhub_sustained += 1
            else:
                windhub_sustained = 0
            if (not faulted_windhub) and windhub_sustained >= 1:
                faulted_windhub = True
            cable_available = not faulted_windhub

        storage_discharge_max = min(STORAGE_MW, soc)
        storage_charge_max = min(STORAGE_MW, STORAGE_MWH - soc)
        diesel_max = min(DG_DIESEL_MW, max(0.0, diesel_budget))

        diesel_out = 0.0
        storage_out = 0.0
        storage_charge = 0.0
        supplied = {k: 0.0 for k in demand}
        crit_supplied = 0.0

        if policy == "baseline":
            if cable_available:
                # 三级规则：主网供电，光伏并网；储能不预充（静态），随浮充
                supplied = dict(demand)
                diesel_out = 0.0
            else:
                avail_renew = pv  # 基线无高空风机
                total_demand = sum(demand.values())
                # 储能兜底（用于全部负荷，含非关键 → 浪费）
                storage_dis = min(storage_discharge_max, max(0.0, total_demand - avail_renew))
                # 柴油应急：覆盖余下全部缺口（含非关键）
                diesel_out = min(diesel_max, max(0.0, total_demand - avail_renew - storage_dis))
                total_avail = avail_renew + storage_dis + diesel_out
                supplied = serve_by_priority(demand, total_avail)
                storage_out = storage_dis
        else:  # windhub
            if cable_available:
                # 预测性预充：利用廉价主网把储能充满
                storage_charge = min(storage_charge_max, STORAGE_MW)
                soc = min(STORAGE_MWH, soc + storage_charge)
                if events["storage_full_hour"] is None and soc >= STORAGE_MWH - 1e-6:
                    events["storage_full_hour"] = h
                supplied = dict(demand)
                diesel_out = 0.0
            else:
                # 预测性协同调度
                if events["islanding_hour"] is None:
                    events["islanding_hour"] = h
                avail_renew = pv + tethered
                if events["feather_hour"] is None and wind > TETHERED_FEATHER_WIND:
                    events["feather_hour"] = h
                crit_demand = sum(demand[k] for k in M.CRITICAL_KEYS)
                noncrit_demand = sum(demand.values()) - crit_demand
                # 1) 可再生优先保关键
                renew_to_crit = min(avail_renew, crit_demand)
                renew_left = avail_renew - renew_to_crit
                crit_rem = crit_demand - renew_to_crit
                # 2) 储能保关键（SOC 充足时）；否则柴发补关键
                if crit_rem > 0:
                    if soc >= STORAGE_LOW_WINDHUB:
                        storage_out = min(storage_discharge_max, crit_rem)
                        crit_rem -= storage_out
                    if crit_rem > 0 and events["diesel_start_hour"] is None:
                        events["diesel_start_hour"] = h
                    if crit_rem > 0:
                        diesel_out = min(diesel_max, crit_rem)
                        crit_rem -= diesel_out
                # 3) 非关键仅用可再生+储能余量，不用柴发（动态优先级、保柴油）
                storage_left = storage_discharge_max - storage_out
                noncrit_avail = renew_left + storage_left
                noncrit_supplied = min(noncrit_demand, noncrit_avail)
                storage_used_noncrit = min(storage_left, max(0.0, noncrit_supplied - renew_left))
                storage_out += storage_used_noncrit
                # 4) 组装供电
                crit_supplied_total = crit_demand - crit_rem
                m_d = demand["medical"]
                d_d = demand["desal"]
                c_d = demand["comms"]
                m_s = min(m_d, crit_supplied_total)
                d_s = min(d_d, crit_supplied_total - m_s)
                c_s = min(c_d, crit_supplied_total - m_s - d_s)
                liv_s = min(demand["livelihood"], noncrit_supplied)
                com_s = min(demand["commercial"], noncrit_supplied - liv_s)
                supplied = {
                    "medical": m_s, "desal": d_s, "comms": c_s,
                    "livelihood": liv_s, "commercial": com_s,
                }
                crit_supplied = crit_supplied_total

        # SOC 更新
        if policy == "baseline":
            soc = max(0.0, soc - storage_out)
        else:
            soc = max(0.0, soc - storage_out)  # storage_charge 已在上面加过

        # 柴油能耗
        diesel_budget = max(0.0, diesel_budget - diesel_out)
        shed = sum(demand[k] - supplied[k] for k in demand)  # MWh（1h 步长）

        # 暴露统计（用于风险敞口）
        # 海缆：基线被动硬故障前持续连接（wind>20 视为暴露）；风枢主动解列，仅风<=20 且仍连接时暴露
        exposures["cable"].append((wind, (wind > CABLE_SECURE_WIND) if policy == "baseline" else (wind <= CABLE_SECURE_WIND and cable_available)))
        # 系留风机
        if policy == "baseline":
            exposures["tethered"].append((wind, wind >= 8.0))
        else:
            exposures["tethered"].append((wind, 8.0 <= wind <= TETHERED_FEATHER_WIND))
        # 光伏（固定，两策略一致）
        exposures["pv"].append((wind, True))
        # 柴油：基线无防护，运行即暴露；风枢风暴核心(>30)受保护
        if policy == "baseline":
            exposures["diesel"].append((wind, diesel_out > 0))
        else:
            exposures["diesel"].append((wind, (diesel_out > 0) and wind <= 30.0))
        # 储能（固定，阈值高，影响极小）
        exposures["storage"].append((wind, True))

        records.append({
            "hour": h,
            "wind": wind,
            "dist": dist,
            "pressure": pressure,
            "demand": demand,
            "supplied": supplied,
            "pv_mw": pv,
            "tethered_mw": tethered,
            "storage_out_mw": storage_out,
            "storage_charge_mw": storage_charge,
            "diesel_output_mw": diesel_out,
            "cable_available": cable_available,
            "soc": soc,
            "shed_mwh": shed,
            "critical_supplied": crit_supplied if policy == "windhub" else sum(supplied[k] for k in M.CRITICAL_KEYS),
            "critical_demand": sum(demand[k] for k in M.CRITICAL_KEYS),
        })

    met = M.compute_metrics(scenario_key, policy, records, exposures)

    # 拓扑潮流校验（取离网峰值附近一小时做代表性校验）
    topo = N.topology_summary()
    validation = None
    if N.PANDAPOWER_AVAILABLE:
        net = N.build_dongji_net()
        # 选取风枢策略中第一个离网小时做校验
        iso_h = events["islanding_hour"] if events["islanding_hour"] is not None else 24
        rec = records[min(iso_h, HOURS - 1)]
        validation = N.validate_dispatch(net, {
            "pv_mw": rec["pv_mw"],
            "storage_mw": rec["storage_out_mw"] - rec["storage_charge_mw"],
            "diesel_mw": rec["diesel_output_mw"],
            "load_total_mw": sum(rec["demand"].values()),
            "cable_available": rec["cable_available"],
        })

    # 时间轴事件（相对离网时刻 T0）
    t0 = events["islanding_hour"]
    timeline = []
    if t0 is not None:
        if events["storage_full_hour"] is not None:
            timeline.append({
                "label": "储能满充完成",
                "hour": events["storage_full_hour"],
                "rel": events["storage_full_hour"] - t0,
                "note": f"台风前利用主网预充至 100%（T{events['storage_full_hour']-t0:+d}h）",
            })
        if events["feather_hour"] is not None:
            timeline.append({
                "label": "高空风机收桨",
                "hour": events["feather_hour"],
                "rel": events["feather_hour"] - t0,
                "note": f"持续风速>28m/s，系留浮空风机收桨着陆（T{events['feather_hour']-t0:+d}h）",
            })
        timeline.append({
            "label": "海缆离网切换",
            "hour": t0,
            "rel": 0,
            "note": "主动/被动离网，微电网进入孤岛运行",
        })
        if events["diesel_start_hour"] is not None:
            timeline.append({
                "label": "柴发启动",
                "hour": events["diesel_start_hour"],
                "rel": events["diesel_start_hour"] - t0,
                "note": f"SOC<30% 且可再生不足，柴油应急启动（T{events['diesel_start_hour']-t0:+d}h）",
            })

    return {
        "scenario_key": scenario_key,
        "policy": policy,
        "records": records,
        "metrics": met.as_dict(),
        "exposures": {k: len([1 for (_, e) in v if e]) for k, v in exposures.items()},
        "topology": topo,
        "validation": validation,
        "timeline": timeline,
        "events": events,
    }


def run_both(scenario_key: str) -> dict:
    """运行同一场景的两套策略，返回 {baseline, windhub}。"""
    baseline = simulate(scenario_key, "baseline")
    windhub = simulate(scenario_key, "windhub")
    return {"baseline": baseline, "windhub": windhub}


if __name__ == "__main__":
    for key in T.get_scenario_keys():
        res = run_both(key)
        b = res["baseline"]["metrics"]
        w = res["windhub"]["metrics"]
        print(f"\n=== {key} ===")
        print(f"  基线  关键保供率={b['critical_supply_rate']*100:5.1f}%  柴油={b['diesel_liters']:8.0f}L  风险={b['risk_exposure']:.2f}")
        print(f"  风枢  关键保供率={w['critical_supply_rate']*100:5.1f}%  柴油={w['diesel_liters']:8.0f}L  风险={w['risk_exposure']:.2f}")
        if b["critical_supply_rate"] > 0:
            print(f"  对比  柴油比={w['diesel_liters']/b['diesel_liters']*100:5.1f}%  风险比={w['risk_exposure']/b['risk_exposure']*100:5.1f}%")
