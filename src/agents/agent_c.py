# -*- coding: utf-8 -*-
"""Agent C —— 能源调度优化。规则引擎 + 可选 LLM 增强。"""
from __future__ import annotations

from . import rule_engine as RE
from . import llm_client as LC
from ..data import typhoons as T


def run(scenario_key: str, use_llm: bool = False) -> dict:
    rule = RE.agent_c_analysis(scenario_key)
    sc = T.get_scenario(scenario_key)
    if use_llm and LC.is_configured():
        return LC.call_agent_llm("C", rule, sc.name)
    return rule
