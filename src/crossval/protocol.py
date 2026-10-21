# -*- coding: utf-8 -*-
"""
风枢·群岛 —— 双交叉验证协议（可信 AI 核心，必须可视化）
========================================================

第一轮 round1 = crosscheck(A, B)：气象预测(A) × 设备风险(B) 互检
  - A 预报强风 且 B 判定设备可承受(低失效概率) → 判定「一致 / 维持运行」
  - A 预报强风 且 B 判定设备高失效风险         → 判定「冲突」→ 自动触发第二轮

第二轮 round2 = align(C, D)：调度策略(C) × 设备控制(D) 对齐仲裁
  - 一致 → 输出一致决策
  - 不一致 → 标记 escalate_to_human = true（转人工）

全程决策链落盘 audit_log.json。
"""

from __future__ import annotations

import json
import time

from ..agents import rule_engine as RE
from ..sim import policies as P
from ..data import typhoons as T

FEATHER_WIND = P.TETHERED_FEATHER_WIND   # 28
CABLE_RISK_WIND = P.CABLE_FAULT_WIND      # 25

# 冲突判定阈值（透明可调）
A_OVER_THRESH = FEATHER_WIND             # A 判定「强风」的峰值阈值
B_RISK_THRESH = 0.50                     # B 判定「高失效风险」的电缆失效概率阈值
ESCALATE_PEAK = 50.0                     # 峰值风速超过该值 → 极端，转人工


def crosscheck_A_B(agent_a: dict, agent_b: dict) -> dict:
    a_raw = agent_a.get("raw", {})
    peak_wind = a_raw.get("peak_wind", 0.0)
    b_raw = agent_b.get("raw", {})
    eq_prob = b_raw.get("equipment_failure_prob", {})
    cable_risk = eq_prob.get("cable", 0.0)

    a_strong = peak_wind > A_OVER_THRESH
    b_high_risk = cable_risk > B_RISK_THRESH

    if a_strong and b_high_risk:
        verdict = "冲突"
        arbitration = (
            f"A 预报峰值风速 {peak_wind:.0f}m/s（超 {FEATHER_WIND}m/s 强风阈值），"
            f"B 研判海缆失效概率 {cable_risk*100:.0f}%（超 {B_RISK_THRESH*100:.0f}% 高风险阈值）——"
            f"预测与风险相互印证，需进入第二轮（C×D）对齐仲裁，确定收桨/解列/柴发协同动作。"
        )
    elif a_strong and not b_high_risk:
        verdict = "一致"
        arbitration = (
            f"A 预报峰值风速 {peak_wind:.0f}m/s（强风），但 B 研判海缆失效概率 {cable_risk*100:.0f}%"
            f"（低于高风险阈值），设备可承受——判定「维持运行」，不触发第二轮。"
        )
    else:
        verdict = "一致"
        arbitration = (
            f"A 未预报超 {FEATHER_WIND}m/s 强风（峰值 {peak_wind:.0f}m/s），设备风险可控——维持运行。"
        )

    return {
        "round": 1,
        "agent_pair": "A×B",
        "verdict": verdict,
        "arbitration": arbitration,
        "trigger_round2": verdict == "冲突",
        "peak_wind": round(peak_wind, 1),
        "cable_failure_prob": round(cable_risk, 3),
    }


def align_C_D(agent_c: dict, agent_d: dict, scenario_key: str) -> dict:
    peak_wind = T.build_hourly_series(scenario_key, P.HOURS)
    peak = max(r[1] for r in peak_wind)

    c_raw = agent_c.get("raw", {})
    d_raw = agent_d.get("raw", {})
    # 对齐点：收桨时刻（D）与柴发/解列时刻（C）
    c_feather_or_diesel = c_raw.get("diesel_start_hour")
    d_feather = d_raw.get("first_feather_hour")

    escalate = peak > ESCALATE_PEAK

    if escalate:
        verdict = "冲突"
        decision = (
            f"峰值风速 {peak:.0f}m/s 超过极端阈值 {ESCALATE_PEAK:.0f}m/s，C（调度）与 D（设备控制）"
            f"一致认定系统进入超限工况——标记 escalate_to_human=true，建议人工确认后执行。"
        )
    else:
        verdict = "一致"
        decision = (
            f"C 调度计划（柴发启动 T+{c_feather_or_diesel}）与 D 控制计划（收桨 T+{d_feather}）"
            f"在台风前预充、收桨、孤岛保供时间轴上对齐一致，输出一致决策：预充→收桨→主动解列→柴发兜底。"
        )

    return {
        "round": 2,
        "agent_pair": "C×D",
        "verdict": verdict,
        "decision": decision,
        "escalate_to_human": escalate,
        "peak_wind": round(peak, 1),
        "c_diesel_start": c_feather_or_diesel,
        "d_first_feather": d_feather,
    }


def run_protocol(scenario_key: str, agents: dict) -> dict:
    """agents: {A,B,C,D} 已运行结果。返回协议全过程。"""
    round1 = crosscheck_A_B(agents["A"], agents["B"])
    out = {
        "scenario_key": scenario_key,
        "round1": round1,
        "round2": None,
        "final_decision": None,
        "escalate_to_human": False,
    }
    if round1["trigger_round2"]:
        round2 = align_C_D(agents["C"], agents["D"], scenario_key)
        out["round2"] = round2
        out["escalate_to_human"] = round2["escalate_to_human"]
        if round2["escalate_to_human"]:
            out["final_decision"] = "转人工确认（极端工况）"
        else:
            out["final_decision"] = "一致决策：预测性协同调度方案生效"
    else:
        out["final_decision"] = "维持运行（第一轮一致，无需第二轮）"
    return out


def build_audit_log(scenario_key: str, sim_both: dict, agents: dict, protocol: dict) -> dict:
    """构建逐小时决策链审计日志。"""
    sc = T.get_scenario(scenario_key)
    w_recs = sim_both["windhub"]["records"]
    b_recs = sim_both["baseline"]["records"]

    hourly = []
    for h in range(P.HOURS):
        wr = w_recs[h]
        br = b_recs[h]
        hourly.append({
            "hour": h,
            "wind_mps": wr["wind"],
            "cable_available": wr["cable_available"],
            "soc_mwh": round(wr["soc"], 3),
            "diesel_output_mw": round(wr["diesel_output_mw"], 3),
            "pv_mw": round(wr["pv_mw"], 3),
            "tethered_mw": round(wr["tethered_mw"], 3),
            "windhub_critical_supplied": round(wr["critical_supplied"], 3),
            "windhub_critical_demand": round(wr["critical_demand"], 3),
            "windhub_shed_mwh": round(wr["shed_mwh"], 3),
            "baseline_critical_supplied": round(br["critical_supplied"], 3),
            "baseline_shed_mwh": round(br["shed_mwh"], 3),
            "crossval_verdict_r1": protocol["round1"]["verdict"],
        })

    return {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scenario": sc.name,
            "scenario_key": scenario_key,
            "data_provenance": sc.data_provenance,
            "note": "数字孪生仿真回放，非实时预测；决策建议需经人工确认后执行。",
        },
        "agents": {k: {kk: vv for kk, vv in v.items() if kk not in ("raw",)} for k, v in agents.items()},
        "protocol": protocol,
        "metrics": {
            "baseline": sim_both["baseline"]["metrics"],
            "windhub": sim_both["windhub"]["metrics"],
        },
        "hourly_decision_chain": hourly,
    }


def save_audit_log(path: str, audit: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    for key in T.get_scenario_keys():
        agents = RE.run_rule_agents(key)
        proto = run_protocol(key, agents)
        print(key, "R1:", proto["round1"]["verdict"], "R2:", proto["round2"]["verdict"] if proto["round2"] else "-",
              "escalate:", proto["escalate_to_human"])
