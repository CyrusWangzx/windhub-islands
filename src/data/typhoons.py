# -*- coding: utf-8 -*-
"""
风枢·群岛 WindHub Islands —— 台风场景库（内置硬编码）
=====================================================

本模块提供 4 个台风场景的 72 小时逐小时时序数据：
  - 风速 (m/s)
  - 台风中心距东极岛距离 (km)
  - 中心气压 (hPa)

设计原则（务必遵守）：
  1. 所有数据均为「基于公开历史数据与参数化建模」的简化回放，非实时预测。
  2. 每个场景都带有 `data_provenance` 字段，说明数据来源与简化方法。
  3. 禁止编造未标注来源的数据；标注来源的数据可经线性/平滑插值简化。

来源说明：
  - 烟花 (2021)：舟山市人民政府灾情通报（全市直接经济损失 16.32 亿元，17.62 万户次停电）。
  - 灿鸿 (2015)：中央气象台路径集（超强台风，朱家尖登陆）。
  - 米娜 (2019)：中央气象台路径集（普陀沈家门登陆）。
  - 兰屿级 (合成)：基于 2023 年台风「小犬」台湾兰屿实测阵风 95.2 m/s 纪录，做参数化外推合成，
    用于极端韧性压力测试，明确标记为合成场景。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Scenario:
    key: str
    name: str
    year: str
    subtitle: str
    summary: str                # 灾情事实卡片文字
    facts: list[str]            # 关键事实（带来源）
    data_provenance: str        # 数据来源与简化方法
    anchor: list[tuple]         # (hour, wind_mps, dist_km, pressure_hpa)
    # 关键负荷时间浮动系数（相对额定值），逐小时；默认 1.0
    load_profile: dict[str, list[float]] = field(default_factory=dict)
    # 设计标记：是否必然在推演中触发 A×B 冲突（临界风速点）
    force_conflict: bool = False


# ---------------------------------------------------------------------------
# 插值工具
# ---------------------------------------------------------------------------
def _interp_anchors(anchor: list[tuple], hours: int = 72) -> list[tuple]:
    """对 (hour, wind, dist, pressure) 锚点做逐小时线性插值，返回 0..hours-1 的小时序列。"""
    anchor = sorted(anchor, key=lambda a: a[0])
    series = []
    for h in range(hours):
        # 找到 h 所在的锚点区间
        if h <= anchor[0][0]:
            w, d, p = anchor[0][1], anchor[0][2], anchor[0][3]
        elif h >= anchor[-1][0]:
            w, d, p = anchor[-1][1], anchor[-1][2], anchor[-1][3]
        else:
            for i in range(len(anchor) - 1):
                h0, w0, d0, p0 = anchor[i]
                h1, w1, d1, p1 = anchor[i + 1]
                if h0 <= h <= h1:
                    t = (h - h0) / (h1 - h0)
                    # 风速用平滑（缓解线性尖角），距离/气压线性
                    w = w0 + (w1 - w0) * (0.5 - 0.5 * math.cos(math.pi * t))  # 平滑插值
                    d = d0 + (d1 - d0) * t
                    p = p0 + (p1 - p0) * t
                    break
        series.append((h, round(w, 2), round(d, 1), round(p, 1)))
    return series


def _default_load_profile(hours: int = 72) -> dict[str, list[float]]:
    """生成默认负荷时间曲线：民生/商业有昼夜波动，医疗/淡水/通信近似恒定。"""
    medical, desal, comms, livelihood, commercial = [], [], [], [], []
    for h in range(hours):
        # 昼夜因子：白天高、夜间低
        day = 0.5 - 0.5 * math.cos(2 * math.pi * (h - 6) / 24)  # 0(夜)~1(午)
        medical.append(1.0)
        desal.append(1.0 + 0.05 * (day - 0.5))
        comms.append(1.0 + 0.1 * (day - 0.5))
        livelihood.append(0.85 + 0.3 * day)
        commercial.append(0.6 + 0.5 * day)
    return {
        "medical": medical,
        "desal": desal,
        "comms": comms,
        "livelihood": livelihood,
        "commercial": commercial,
    }


# ---------------------------------------------------------------------------
# 场景定义
# ---------------------------------------------------------------------------
SCENARIOS: dict[str, Scenario] = {}

# 1) 烟花（2021）—— 真实回放主场景
SCENARIOS["yanhua"] = Scenario(
    key="yanhua",
    name="烟花 (2021)",
    year="2021",
    subtitle="舟山真实灾情回放 · 主场景",
    summary=(
        "2021 年第 6 号台风「烟花」是舟山市有气象记录以来过程风雨影响时间最长的台风之一，"
        "全市直接经济损失 16.32 亿元，累计 17.62 万户次停电。本场景以其公开灾情时间线为基准做逐小时简化回放，"
        "用于校准风枢策略的保供与降损目标。"
    ),
    facts=[
        "来源：舟山市人民政府 / 应急管理局灾情通报",
        "全市直接经济损失约 16.32 亿元",
        "累计 17.62 万户次停电",
        "简化方法：以公开通报的登陆—影响时间线为锚，风速/距离/气压做逐小时平滑插值，未引入未标注数据",
    ],
    data_provenance=(
        "数据来源：舟山市人民政府灾情报告、中央气象台（CMA）路径集。简化方法：以公开影响时间线为锚点，"
        "对风速采用余弦平滑插值（避免线性尖角失真），对中心距离与气压采用线性插值；逐小时序列用于仿真回放，"
        "不代表实时观测。"
    ),
    anchor=[
        (0, 8, 600, 1005),
        (12, 15, 450, 998),
        (24, 25, 250, 985),
        (30, 33, 120, 970),
        (36, 35, 80, 968),
        (42, 30, 150, 978),
        (48, 22, 280, 988),
        (54, 18, 300, 990),
        (66, 10, 500, 1000),
        (72, 8, 600, 1005),
    ],
    force_conflict=True,  # 峰值 33~35 m/s 持续 >25 且海缆风险高，设计必然触发 A×B 冲突
)

# 2) 灿鸿（2015）—— 超强台风登陆朱家尖
SCENARIOS["chanhu"] = Scenario(
    key="chanhu",
    name="灿鸿 (2015)",
    year="2015",
    subtitle="超强台风 · 朱家尖登陆",
    summary=(
        "2015 年第 9 号超强台风「灿鸿」在舟山朱家尖登陆，是建国以来登陆舟山的最强台风之一。"
        "本场景以中央气象台路径集为基准，刻画其快速增强与极强风速过程，用于检验系统在超强台风下的韧性边界。"
    ),
    facts=[
        "来源：中央气象台（CMA）台风路径与强度集",
        "登陆等级：超强台风，朱家尖登陆",
        "简化方法：以 CMA 路径集强度锚点做逐小时平滑插值",
    ],
    data_provenance=(
        "数据来源：中央气象台（CMA）台风最佳路径数据集（CMA-STI）。简化方法：以公开最佳路径的"
        "中心气压与最大风速锚点，按时间做逐小时余弦平滑插值得到东极岛近似影响序列（未做站点级降尺度）。"
    ),
    anchor=[
        (0, 10, 700, 1002),
        (18, 22, 400, 988),
        (30, 40, 150, 955),
        (36, 42, 100, 950),
        (42, 38, 180, 962),
        (54, 20, 350, 985),
        (66, 10, 550, 1002),
        (72, 8, 650, 1005),
    ],
)

# 3) 米娜（2019）—— 普陀沈家门登陆
SCENARIOS["mituo"] = Scenario(
    key="mituo",
    name="米娜 (2019)",
    year="2019",
    subtitle="强台风 · 沈家门登陆",
    summary=(
        "2019 年第 18 号台风「米娜」在舟山普陀沈家门登陆，带来强风暴雨与风暴潮。"
        "本场景刻画其中等强度、短时强影响过程，用于对比不同强度下的策略收益差异。"
    ),
    facts=[
        "来源：中央气象台（CMA）台风路径与强度集",
        "登陆地点：舟山普陀沈家门",
        "简化方法：以 CMA 路径集强度锚点做逐小时平滑插值",
    ],
    data_provenance=(
        "数据来源：中央气象台（CMA）台风最佳路径数据集。简化方法：以公开最佳路径锚点做逐小时"
        "余弦平滑插值得到东极岛近似影响序列。"
    ),
    anchor=[
        (0, 9, 550, 1004),
        (14, 18, 380, 992),
        (28, 30, 180, 975),
        (34, 32, 130, 972),
        (40, 26, 220, 982),
        (52, 14, 380, 996),
        (64, 8, 520, 1004),
        (72, 7, 600, 1005),
    ],
)

# 4) 兰屿级（合成极端）—— 95.2 m/s 阵风外推
SCENARIOS["lanyu"] = Scenario(
    key="lanyu",
    name="兰屿级 (合成极端)",
    year="合成",
    subtitle="极端压力测试 · 95.2 m/s 阵风外推",
    summary=(
        "合成极端场景。以 2023 年台风「小犬」在台湾兰屿录得的 95.2 m/s 历史实测阵风为上限，"
        "做参数化外推合成，用于检验微电网在远超常规设计标准下的失效边界与系统可被仲裁/转人工的能力。"
        "本场景为合成数据，明确标记为压力测试用途。"
    ),
    facts=[
        "来源：2023 年台风「小犬」台湾兰屿实测阵风 95.2 m/s（媒体报道/气象站纪录）",
        "性质：合成外推，非真实台风过程",
        "用途：极端韧性压力测试（必然触发多轮交叉验证与转人工）",
    ],
    data_provenance=(
        "数据来源：2023 年台风「小犬」兰屿气象站实测阵风 95.2 m/s（公开媒体报道与气象观测纪录）。"
        "简化方法：以该实测阵风为强度上限，做参数化外推合成一条 72 小时极端风速过程（中心距离/气压按"
        "强度对应关系构造），全程标注为合成数据，仅用于系统韧性边界测试。"
    ),
    anchor=[
        (0, 12, 800, 1000),
        (18, 28, 450, 980),
        (30, 55, 200, 930),
        (36, 60, 150, 925),
        (42, 52, 240, 940),
        (54, 30, 400, 975),
        (66, 14, 600, 998),
        (72, 10, 750, 1003),
    ],
    force_conflict=True,
)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def get_scenario(key: str) -> Scenario:
    if key not in SCENARIOS:
        raise KeyError(f"未知场景: {key}，可选: {list(SCENARIOS.keys())}")
    return SCENARIOS[key]


def get_scenario_keys() -> list[str]:
    return list(SCENARIOS.keys())


def build_hourly_series(key: str, hours: int = 72) -> list[tuple]:
    """返回 [(hour, wind, dist, pressure), ...] 逐小时序列。"""
    sc = get_scenario(key)
    series = _interp_anchors(sc.anchor, hours)
    if not sc.load_profile:
        sc.load_profile = _default_load_profile(hours)
    return series


def build_load_profile(key: str, hours: int = 72) -> dict[str, list[float]]:
    sc = get_scenario(key)
    if not sc.load_profile:
        sc.load_profile = _default_load_profile(hours)
    return sc.load_profile


if __name__ == "__main__":
    for k in get_scenario_keys():
        s = build_hourly_series(k)
        print(k, "peak wind =", max(r[1] for r in s), "m/s")
