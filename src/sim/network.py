# -*- coding: utf-8 -*-
"""
风枢·群岛 —— 电网仿真拓扑（pandapower）
========================================

使用 pandapower 建立两套数字孪生模型：
  1. 东极四岛微电网拓扑（柴发 / 储能 / 光伏 / 海缆等效线路 / 分级负荷）
  2. IEEE 33 节点标准配电系统（作为标准对照模型，一并内置）

注意：
  - 逐小时「调度—供电平衡」由 policies.py 用纯 Python 线性平衡完成（即指标的真实来源）。
  - 本模块的 pandapower 网络用于「拓扑建模 + 关键小时潮流校验」，验证调度方案在
    电网物理层面可行（电压越限 / 线路过载检测），作为数字孪生的物理可信底座。
  - 若运行环境未安装 pandapower（理论上不应发生，requirements 已声明），拓扑校验将优雅跳过。
"""

from __future__ import annotations

import os
import contextlib
import io

try:
    # pandapower 在未安装 numba 时会向 stdout 打印提示，重定向以避免污染日志
    with contextlib.redirect_stdout(io.StringIO()):
        import pandapower as pp
        import numpy as np
    PANDAPOWER_AVAILABLE = True
except Exception:  # pragma: no cover
    pp = None
    np = None
    PANDAPOWER_AVAILABLE = False


# 东极微电网额定参数（与任务规格一致）
DG_DIESEL_MW = 4.3
STORAGE_MW = 1.0
STORAGE_MWH = 2.0
PV_MW = 1.0
CABLE_MW_LIMIT = 5.0  # 海缆等效传输能力（充裕，主供电源）

# 分级负荷额定（MW）
LOADS = {
    "medical": 0.15,
    "desal": 0.30,
    "comms": 0.10,
    "livelihood": 0.90,
    "commercial": 0.50,
}


def build_dongji_net():
    """建立东极四岛微电网 pandapower 模型。返回 net。"""
    if not PANDAPOWER_AVAILABLE:
        return None
    net = pp.create_empty_network()

    # 母线：0=海缆接入(外部电网) 1=岛内主配电 2=负荷汇集
    b_grid = pp.create_bus(net, vn_kv=35.0, name="海缆接入母线")
    b_main = pp.create_bus(net, vn_kv=35.0, name="岛内主配电母线")
    b_load = pp.create_bus(net, vn_kv=10.0, name="负荷汇集母线")

    # 海缆等效线路（外部电网连接）
    pp.create_line_from_parameters(
        net, from_bus=b_grid, to_bus=b_main, length_km=30.0,
        r_ohm_per_km=0.2, x_ohm_per_km=0.4, c_nf_per_km=10.0,
        max_i_ka=0.15, name="35kV海缆等效",
    )
    # 主变等效
    pp.create_transformer_from_parameters(
        net, hv_bus=b_main, lv_bus=b_load, sn_mva=6.0, vn_hv_kv=35.0, vn_lv_kv=10.0,
        vkr_percent=1.0, vk_percent=6.0, pfe_kw=10.0, i0_percent=0.5, name="主变",
    )

    # 外部电网（海缆）作为 slack
    pp.create_ext_grid(net, bus=b_grid, vm_pu=1.0, name="大陆电网(海缆)")

    # 柴油发电机（可控电源，作为潜在 slack）
    pp.create_gen(
        net, bus=b_main, p_mw=0.0, min_p_mw=0.0, max_p_mw=DG_DIESEL_MW,
        vm_pu=1.0, name="柴油发电机4.3MW", slack=False,
    )
    # 光伏（静态电源）
    pp.create_gen(
        net, bus=b_main, p_mw=0.0, min_p_mw=0.0, max_p_mw=PV_MW,
        vm_pu=1.0, name="光伏1MW", slack=False,
    )
    # 储能（用 gen 近似：正为放电，负为充电）
    pp.create_gen(
        net, bus=b_main, p_mw=0.0, min_p_mw=-STORAGE_MW, max_p_mw=STORAGE_MW,
        vm_pu=1.0, name="储能1MW/2MWh", slack=False,
    )

    # 分级负荷（汇到负荷母线，按额定）
    for name, p in LOADS.items():
        pp.create_load(net, bus=b_load, p_mw=p, q_mvar=0.0, name=name)

    return net


def build_ieee33_net():
    """内置 IEEE 33 节点标准配电系统作为对照模型。返回 net。"""
    if not PANDAPOWER_AVAILABLE:
        return None
    try:
        from pandapower.networks import case33bw

        return case33bw()
    except Exception:
        # 某些 pandapower 版本接口不同，尝试备选
        try:
            from pandapower import networks

            return networks.case33bw()
        except Exception:
            return None


def validate_dispatch(net, dispatch: dict):
    """
    对给定小时的调度方案做潮流校验。
    dispatch: {pv_mw, storage_mw(正放电), diesel_mw, load_total_mw, cable_available}
    返回 {converged, max_loading_pct, min_vm_pu, max_vm_pu, note}
    """
    if net is None:
        return {
            "converged": None,
            "max_loading_pct": None,
            "min_vm_pu": None,
            "max_vm_pu": None,
            "note": "pandapower 未安装，拓扑校验跳过",
        }
    try:
        # 设置电源出力
        # gen 顺序：0=柴油 1=光伏 2=储能
        net.gen.at[0, "p_mw"] = dispatch.get("diesel_mw", 0.0)
        net.gen.at[1, "p_mw"] = dispatch.get("pv_mw", 0.0)
        net.gen.at[2, "p_mw"] = dispatch.get("storage_mw", 0.0)  # 正=放电

        # 海缆/外部电网切换
        cable_available = dispatch.get("cable_available", True)
        if cable_available:
            net.ext_grid.at[0, "in_service"] = True
            net.gen.at[0, "slack"] = False
        else:
            net.ext_grid.at[0, "in_service"] = False
            net.gen.at[0, "slack"] = True  # 离网时柴油作平衡机

        pp.runpp(net)
        res = {
            "converged": bool(net.converged),
            "max_loading_pct": float(np.max(net.res_line.loading_percent)) if len(net.res_line) else 0.0,
            "min_vm_pu": float(np.min(net.res_bus.vm_pu)),
            "max_vm_pu": float(np.max(net.res_bus.vm_pu)),
            "note": "潮流计算完成",
        }
        # 恢复外部电网在线，避免影响后续
        net.ext_grid.at[0, "in_service"] = True
        net.gen.at[0, "slack"] = False
        return res
    except Exception as e:  # pragma: no cover
        return {
            "converged": False,
            "max_loading_pct": None,
            "min_vm_pu": None,
            "max_vm_pu": None,
            "note": f"潮流校验异常: {e}",
        }


def topology_summary() -> dict:
    """返回两套拓扑的建模摘要，供前端展示。"""
    dongji = build_dongji_net()
    ieee33 = build_ieee33_net()
    return {
        "pandapower_available": PANDAPOWER_AVAILABLE,
        "dongji_buses": int(len(dongji.bus)) if dongji is not None else 0,
        "dongji_gens": int(len(dongji.gen)) if dongji is not None else 0,
        "dongji_loads": int(len(dongji.load)) if dongji is not None else 0,
        "ieee33_buses": int(len(ieee33.bus)) if ieee33 is not None else 0,
        "ieee33_lines": int(len(ieee33.line)) if ieee33 is not None else 0,
    }
