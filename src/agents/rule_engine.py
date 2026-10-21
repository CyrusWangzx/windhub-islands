# -*- coding: utf-8 -*-
"""
风枢·群岛 —— 规则引擎（默认模式，无 API Key 必须可用）
====================================================

基于风速时序 + 设备阈值的确定性决策，输出专业可信的中文分析文本与结构化结果。
所有 Agent 在「规则引擎模式」下由本模块驱动；「LLM 增强模式」下本模块输出作为
grounding 传入 LLM system prompt（见 llm_client.py）。

阈值（透明、可调，与 policies.py / metrics.py 一致）：
  收桨风速 > 28 m/s；海缆风险起算 > 25 m/s；光伏衰减起算 > 20 m/s。
"""

from __future__ import annotations

import math
from statistics import mean, pstdev

from ..data import typhoons as T
from ..sim import metrics as M
from ..sim import policies as P

FEATHER_WIND = P.TETHERED_FEATHER_WIND      # 28
CABLE_RISK_WIND = P.CABLE_FAULT_WIND         # 25
PV_ATT_WIND = 20.0


# ---------------------------------------------------------------------------
# Agent A —— 气象态势感知
# ---------------------------------------------------------------------------
def agent_a_analysis(scenario_key: str) -> dict:
    series = T.build_hourly_series(scenario_key, P.HOURS)
    winds = [r[1] for r in series]

    def window_stats(start, end):
        seg = winds[start:end]
        if not seg:
            return 0.0, 0.0, 0.0
        return mean(seg), min(seg), max(seg)

    def over_thresh_prob(start, end, thr=FEATHER_WIND):
        seg = winds[start:end]
        if not seg:
            return 0.0
        return sum(1 for w in seg if w > thr) / len(seg)

    m24, lo24, hi24 = window_stats(0, 24)
    m48, lo48, hi48 = window_stats(24, 48)
    m72, lo72, hi72 = window_stats(48, 72)
    p24 = over_thresh_prob(0, 24)
    p48 = over_thresh_prob(24, 48)
    p72 = over_thresh_prob(48, 72)

    peak = max(winds)
    peak_h = winds.index(peak)

    findings = [
        f"未来 24h 风速区间约 {lo24:.0f}–{hi24:.0f} m/s，均值 {m24:.1f} m/s；超 28m/s 阈值概率 {p24*100:.0f}%。",
        f"未来 48h 风速区间约 {lo48:.0f}–{hi48:.0f} m/s，均值 {m48:.1f} m/s；超阈值概率 {p48*100:.0f}%。",
        f"未来 72h 风速区间约 {lo72:.0f}–{hi72:.0f} m/s，均值 {m72:.1f} m/s；超阈值概率 {p72*100:.0f}%。",
        f"预计第 {peak_h}h 出现风速峰值 {peak:.1f} m/s（中心距岛最近、气压最低时段）。",
    ]
    recs = []
    if p48 > 0.3 or p72 > 0.3:
        recs.append("建议启动台风前预充与收桨预案：在峰值来临前完成储能满充、高空风机收桨着陆。")
    if peak > CABLE_RISK_WIND:
        recs.append(f"峰值风速 {peak:.0f} m/s 超过海缆风险起算值 {CABLE_RISK_WIND} m/s，需评估主动解列窗口。")
    recs.append("建议持续监视逐小时风速，触发双交叉验证第一轮（A×B）互检。")

    trace = (
        f"规则引擎：基于内置历史/合成时序（来源见场景 data_provenance），对 72h 逐小时风速做窗口统计。"
        f"以 {FEATHER_WIND}m/s 为强风阈值，计算各时段超阈值概率；峰值判定取序列最大值。"
    )
    return {
        "agent": "A",
        "name": "气象态势感知",
        "status": "已生成结构化概率预报",
        "confidence": round(0.80 + 0.1 * (1 - max(p24, p48, p72)), 2),
        "key_findings": findings,
        "recommendations": recs,
        "reasoning_trace": trace,
        "raw": {
            "horizons": {
                "24h": {"mean": round(m24, 1), "lo": round(lo24, 1), "hi": round(hi24, 1), "p_over": round(p24, 2)},
                "48h": {"mean": round(m48, 1), "lo": round(lo48, 1), "hi": round(hi48, 1), "p_over": round(p48, 2)},
                "72h": {"mean": round(m72, 1), "lo": round(lo72, 1), "hi": round(hi72, 1), "p_over": round(p72, 2)},
            },
            "peak_wind": round(peak, 1),
            "peak_hour": peak_h,
        },
    }


# ---------------------------------------------------------------------------
# Agent B —— 设备健康与风险
# ---------------------------------------------------------------------------
def agent_b_analysis(scenario_key: str) -> dict:
    series = T.build_hourly_series(scenario_key, P.HOURS)
    winds = [r[1] for r in series]
    peak = max(winds)
    peak_h = winds.index(peak)

    # 各设备峰值风速下失效概率
    eq_probs = {e: M.failure_prob(e, peak) for e in M.EQUIPMENT_MODELS}
    # 海缆：风速>25 持续小时数
    cable_stress_hours = sum(1 for w in winds if w > CABLE_RISK_WIND)

    findings = [
        f"海缆：峰值风速 {peak:.0f} m/s，风速>{CABLE_RISK_WIND}m/s 累计 {cable_stress_hours}h，"
        f"当前失效概率约 {eq_probs['cable']*100:.0f}%。",
        f"系留浮空风机：> {FEATHER_WIND}m/s 需收桨，峰值远超阈值，未收桨时失效概率约 {eq_probs['tethered']*100:.0f}%。",
        f"光伏阵列：> {PV_ATT_WIND}m/s 出力衰减，>30m/s 组件损伤概率约 {eq_probs['pv']*100:.0f}%。",
        f"柴油发电机：低洼布置，风暴潮进水风险约 {eq_probs['diesel']*100:.0f}%（需预防护）。",
    ]
    # 反事实分析
    cf_wind = min(peak + 5.0, 70.0)
    cf_prob = M.failure_prob("cable", cf_wind)
    counterfactual = (
        f"反事实：若第 {peak_h+6}h 起持续风速升至 {cf_wind:.0f} m/s，海缆失效概率将由 "
        f"{eq_probs['cable']*100:.0f}% 升至约 {cf_prob*100:.0f}%，建议提前 {6}h 主动解列以规避。"
    )
    recs = [
        "建议在第 {0}h 峰值前完成高空风机收桨与柴油机组防洪加固。".format(peak_h - 6 if peak_h > 6 else 0),
        "建议海缆在风速>25m/s 持续前主动解列，降低风暴核心暴露。",
        "将设备风险研判结果提交第二轮交叉验证（C×D）仲裁。",
    ]
    trace = (
        f"规则引擎：基于 metrics.EQUIPMENT_MODELS 失效概率曲线，取峰值风速 {peak:.0f}m/s 评估各设备失效概率；"
        f"反事实分析以峰值+6h 风速外推，演示『若 X 小时后强风，Y 设备会怎样』。"
    )
    return {
        "agent": "B",
        "name": "设备健康与风险",
        "status": "已生成健康度与失效概率",
        "confidence": round(0.78 + 0.12 * eq_probs["cable"], 2),
        "key_findings": findings,
        "recommendations": recs,
        "reasoning_trace": trace,
        "counterfactual": counterfactual,
        "raw": {
            "equipment_failure_prob": {k: round(v, 3) for k, v in eq_probs.items()},
            "cable_stress_hours": cable_stress_hours,
            "peak_wind": round(peak, 1),
        },
    }


# ---------------------------------------------------------------------------
# Agent C —— 能源调度优化
# ---------------------------------------------------------------------------
def agent_c_analysis(scenario_key: str) -> dict:
    sim = P.simulate(scenario_key, "windhub")
    recs_w = sim["records"]
    events = sim["events"]
    metrics = sim["metrics"]

    # 储能计划摘要
    soc_series = [r["soc"] for r in recs_w]
    full_h = events["storage_full_hour"]
    diesel_h = events["diesel_start_hour"]
    iso_h = events["islanding_hour"]

    findings = [
        f"储能计划：台风前（第 {full_h}h）预充至 100%（2MWh），孤岛期按关键负荷缺口放电，"
        f"谷值 SOC 约 {min(soc_series):.2f} MWh。",
        f"负荷优先级：动态分级——医疗>淡化>通信>民生>商业；孤岛期优先保关键负荷、动态切除非关键。",
        f"柴油备用：延迟至 SOC<30% 且可再生不足时启动（第 {diesel_h}h），仅补足关键负荷缺口。",
        f"预测性收益：关键负荷保供率 {metrics['critical_supply_rate']*100:.0f}%，"
        f"柴油消耗 {metrics['diesel_liters']:.0f} L。",
    ]
    recs = [
        "按时间轴在 T-18h 前完成储能满充，T-6h 前完成高空风机收桨。",
        "孤岛运行期间严禁柴油用于非关键负荷，保关键负荷优先。",
        "将调度方案与 Agent D 设备控制对齐，进入第二轮仲裁。",
    ]
    trace = (
        "规则引擎：读取 windhub_policy 72h 仿真记录，提取储能 SOC 曲线、离网/柴发启动时刻与指标，"
        "生成调度计划摘要。动态优先级在 policies.simulate 中通过关键负荷优先分配实现。"
    )
    return {
        "agent": "C",
        "name": "能源调度优化",
        "status": "已生成 72h 调度计划",
        "confidence": 0.85,
        "key_findings": findings,
        "recommendations": recs,
        "reasoning_trace": trace,
        "raw": {
            "storage_full_hour": full_h,
            "islanding_hour": iso_h,
            "diesel_start_hour": diesel_h,
            "min_soc_mwh": round(min(soc_series), 2),
            "critical_supply_rate": metrics["critical_supply_rate"],
            "diesel_liters": metrics["diesel_liters"],
        },
    }


# ---------------------------------------------------------------------------
# Agent D —— 高空风能管控
# ---------------------------------------------------------------------------
def agent_d_analysis(scenario_key: str) -> dict:
    series = T.build_hourly_series(scenario_key, P.HOURS)
    winds = [r[1] for r in series]
    feasible = [h for h, w in enumerate(winds) if 8.0 <= w <= FEATHER_WIND]
    over = [h for h, w in enumerate(winds) if w > FEATHER_WIND]
    total_feasible_mwh = len(feasible) * P.TETHERED_MW

    findings = [
        f"可行发电窗口：风速 8–{FEATHER_WIND}m/s 的安全小时数约 {len(feasible)}h，"
        f"理论可增发约 {total_feasible_mwh:.1f} MWh。",
        f"收桨阈值：持续风速 > {FEATHER_WIND}m/s 共 {len(over)}h，须收桨着陆规避结构失效。",
        "重部署建议：台风中心过境、风速回撤至 <20m/s 且阵风减弱后，重新放飞系留浮空风机。",
    ]
    recs = [
        f"在第 {over[0]}h（首个 >{FEATHER_WIND}m/s 时刻）前完成收桨着陆。",
        "收桨期间将发电缺口交由预充储能与柴油备用覆盖。",
        "与 Agent C 对齐：收桨/重部署动作纳入调度时间轴。",
    ]
    trace = (
        f"规则引擎：扫描 72h 风速序列，统计 8–{FEATHER_WIND}m/s 可行窗口与 >{FEATHER_WIND}m/s 收桨窗口，"
        f"按 TETHERED_MW={P.TETHERED_MW}MW 估算可增发电量。"
    )
    return {
        "agent": "D",
        "name": "高空风能管控",
        "status": "已生成发电窗口与收桨建议",
        "confidence": 0.82,
        "key_findings": findings,
        "recommendations": recs,
        "reasoning_trace": trace,
        "raw": {
            "feasible_hours": len(feasible),
            "feather_hours": len(over),
            "first_feather_hour": over[0] if over else None,
            "potential_mwh": round(total_feasible_mwh, 1),
        },
    }


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def run_rule_agents(scenario_key: str) -> dict:
    return {
        "A": agent_a_analysis(scenario_key),
        "B": agent_b_analysis(scenario_key),
        "C": agent_c_analysis(scenario_key),
        "D": agent_d_analysis(scenario_key),
    }
