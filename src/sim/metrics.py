# -*- coding: utf-8 -*-
"""
风枢·群岛 —— 指标口径模块（诚实口径，禁止篡改公式）
====================================================

所有指标均由仿真逐小时记录真实计算得出，不对公式做凑数修改。
口径定义见 README「指标口径与校准说明」。

关键负荷 = 医疗 + 海水淡化 + 通信基站（最高优先级三类）
"""

from __future__ import annotations

from dataclasses import dataclass, field

CRITICAL_KEYS = ["medical", "desal", "comms"]
ALL_KEYS = ["medical", "desal", "comms", "livelihood", "commercial"]

# 柴油机油耗率（L/kWh），来自任务规格
DIESEL_FUEL_RATE = 0.25

# 设备失效概率模型（风速 -> 失效概率），用于风险敞口计算
# 该函数是「物理/经验简化」的透明建模，可解释、可调参，但不篡改指标定义本身。
EQUIPMENT_MODELS = {
    # 海缆：风速 > 25 m/s 后失效概率随持续强度上升（台风中锚链/杆塔风险）
    "cable": {"threshold": 25.0, "max_prob": 0.90, "slope": 1 / 18.0},
    # 系留浮空风机：持续风速 > 28 m/s 建议收桨，否则结构失效概率骤升
    "tethered": {"threshold": 28.0, "max_prob": 0.95, "slope": 1 / 30.0},
    # 光伏阵列：风速 > 30 m/s 后组件损伤概率上升
    "pv": {"threshold": 30.0, "max_prob": 0.80, "slope": 1 / 40.0},
    # 柴油发电机：低洼布置，风暴潮/进水风险（风枢策略会预先防护）
    "diesel": {"threshold": 32.0, "max_prob": 0.60, "slope": 1 / 30.0},
    # 储能：基本不受风直接影响
    "storage": {"threshold": 45.0, "max_prob": 0.30, "slope": 1 / 60.0},
}


def failure_prob(equipment: str, wind: float) -> float:
    """给定设备与当前风速，返回该小时该设备的失效概率（0~max_prob）。"""
    m = EQUIPMENT_MODELS.get(equipment)
    if m is None:
        return 0.0
    if wind <= m["threshold"]:
        return 0.0
    p = (wind - m["threshold"]) * m["slope"]
    return min(m["max_prob"], p)


@dataclass
class Metrics:
    scenario_key: str
    policy: str
    # 关键负荷保供率（0~1）
    critical_supply_rate: float = 0.0
    # 总保供率（0~1）
    total_supply_rate: float = 0.0
    # 柴油消耗（升）
    diesel_liters: float = 0.0
    # 柴油发电量（MWh）
    diesel_energy_mwh: float = 0.0
    # 风险敞口（加权：暴露时长 × 失效概率）
    risk_exposure: float = 0.0
    # 切负荷总量（MWh）
    shed_energy_mwh: float = 0.0
    # 设备暴露时长（小时），按设备聚合
    equipment_exposure: dict = field(default_factory=dict)
    # 决策耗时标注（仿真侧为秒级，基线为分钟级人工）
    decision_latency_label: str = ""

    def as_dict(self) -> dict:
        return {
            "scenario_key": self.scenario_key,
            "policy": self.policy,
            "critical_supply_rate": round(self.critical_supply_rate, 4),
            "total_supply_rate": round(self.total_supply_rate, 4),
            "diesel_liters": round(self.diesel_liters, 1),
            "diesel_energy_mwh": round(self.diesel_energy_mwh, 2),
            "risk_exposure": round(self.risk_exposure, 4),
            "shed_energy_mwh": round(self.shed_energy_mwh, 2),
            "equipment_exposure": {k: round(v, 2) for k, v in self.equipment_exposure.items()},
            "decision_latency_label": self.decision_latency_label,
        }


def compute_metrics(
    scenario_key: str,
    policy: str,
    records: list[dict],
    exposures: dict = None,
) -> Metrics:
    """
    由逐小时仿真记录计算指标。

    records: 每条含
      - demand: {cat: MW}
      - supplied: {cat: MW}
      - diesel_output_mw: float
      - shed_mwh: float (本小时切负荷量)
      - wind: float
     exposures: {equipment: 暴露小时数}（来自策略层的暴露统计）
    """
    crit_demand = 0.0
    crit_supplied = 0.0
    total_demand = 0.0
    total_supplied = 0.0
    diesel_mwh = 0.0
    shed_mwh = 0.0

    for r in records:
        d = r["demand"]
        s = r["supplied"]
        for cat in ALL_KEYS:
            dem = d.get(cat, 0.0)
            sup = s.get(cat, 0.0)
            total_demand += dem
            total_supplied += sup
            if cat in CRITICAL_KEYS:
                crit_demand += dem
                crit_supplied += sup
        diesel_mwh += r.get("diesel_output_mw", 0.0)  # 1h 步长
        shed_mwh += r.get("shed_mwh", 0.0)

    diesel_liters = diesel_mwh * 1000.0 * DIESEL_FUEL_RATE

    # 风险敞口：Σ_h Σ_e exposure(e,h) × failure_prob(e, wind_h)
    risk = 0.0
    equip_exposure = {}
    if exposures:
        for equip, hours_list in exposures.items():
            exp_hours = 0.0
            for (wind_at_h, exposed) in hours_list:
                if exposed:
                    exp_hours += 1.0
                    risk += 1.0 * failure_prob(equip, wind_at_h)
            equip_exposure[equip] = exp_hours

    m = Metrics(
        scenario_key=scenario_key,
        policy=policy,
        critical_supply_rate=(crit_supplied / crit_demand) if crit_demand > 0 else 1.0,
        total_supply_rate=(total_supplied / total_demand) if total_demand > 0 else 1.0,
        diesel_liters=diesel_liters,
        diesel_energy_mwh=diesel_mwh,
        risk_exposure=risk,
        shed_energy_mwh=shed_mwh,
        equipment_exposure=equip_exposure,
    )
    m.decision_latency_label = "秒级（自动）" if policy == "windhub" else "分钟级（人工）"
    return m
