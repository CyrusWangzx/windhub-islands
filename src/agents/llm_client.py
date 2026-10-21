# -*- coding: utf-8 -*-
"""
风枢·群岛 —— LLM 客户端（增强模式，可选）
==========================================

通过 OpenAI 兼容接口（chat.completions）调用 LLM 增强四个 Agent。
配置（.env）：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。
未配置或调用/解析失败时，自动回退规则引擎结果并记录（禁止伪装 LLM 调用）。

设计纪律：
  - 规则引擎的结构化输出作为 grounding 传入 system prompt（『你是…，以下是仿真基线数据…』）。
  - LLM 输出必须是可解析 JSON；解析失败 → 回退规则引擎结果，并在日志标记 fallback。
  - 全程不依赖联网下载其它数据；仅向用户配置的 LLM 端点发起请求。
"""

from __future__ import annotations

import json
import os
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

FALLBACK_LOG = []


def is_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def _config() -> dict:
    return {
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
    }


SYSTEM_PROMPT = (
    "你是风枢·群岛台风韧性微电网多智能体系统中的一名专家 Agent。"
    "以下是仿真基线数据（由规则引擎确定性计算得到，务必以其为事实基础，不得编造数字）：\n"
    "{grounding}\n"
    "请基于以上事实，输出严格 JSON，结构如下：\n"
    '{{"agent":"{agent}","status":"...","confidence":0.0~1.0,'
    '"key_findings":["..."],"recommendations":["..."],"reasoning_trace":"中文可读推理过程"}}\n'
    "只输出 JSON，不要多余文字。"
)


def call_agent_llm(agent_letter: str, rule_result: dict, scenario_name: str) -> dict:
    """
    调用 LLM 增强单个 Agent。返回 LLM 解析结果；任何异常/解析失败返回 rule_result（回退）并记日志。
    """
    if not is_configured():
        # 未配置：不伪装，直接返回规则引擎结果（上层据此显示「规则引擎模式」徽标）
        return rule_result

    cfg = _config()
    grounding = json.dumps(rule_result.get("raw", rule_result), ensure_ascii=False)
    system = SYSTEM_PROMPT.format(grounding=grounding, agent=agent_letter)
    user = f"场景：{scenario_name}。请作为 Agent {agent_letter}（{rule_result.get('name','')}）给出增强分析。"

    try:
        resp = requests.post(
            f"{cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        # 校验关键字段
        for fld in ("agent", "status", "confidence", "key_findings", "recommendations", "reasoning_trace"):
            if fld not in parsed:
                raise ValueError(f"LLM 返回缺少字段: {fld}")
        parsed["llm_enhanced"] = True
        return parsed
    except Exception as e:
        FALLBACK_LOG.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "agent": agent_letter,
            "error": str(e),
            "action": "回退规则引擎结果",
        })
        # 回退：保留规则引擎结果，标记
        rule_result = dict(rule_result)
        rule_result["llm_enhanced"] = False
        rule_result["fallback_reason"] = str(e)
        return rule_result


def get_fallback_log() -> list:
    return FALLBACK_LOG
