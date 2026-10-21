# -*- coding: utf-8 -*-
"""
风枢·群岛 WindHub Islands —— 东极岛台风 72 小时沙盘推演看板
===========================================================

面向台风极端天气的海岛韧性微电网多智能体预测性协同调度系统（AI4Climate 参赛作品）。
运行：pip install -r requirements.txt && python app.py（无 API Key 亦可完整演示）。

前端：Gradio Blocks，三个 Tab + 顶部场景选择器，全中文界面。
"""

from __future__ import annotations

import os
import tempfile

import gradio as gr
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.data import typhoons as T
from src.sim import policies as P
from src.sim import network as N
from src.agents import agent_a, agent_b, agent_c, agent_d
from src.agents import llm_client as LC
from src.crossval import protocol as X

# —— 模式徽标 ——
USE_LLM = LC.is_configured()
MODE_BADGE = "🟢 LLM 增强模式" if USE_LLM else "🟡 规则引擎模式（未配置 API）"

FOOTER = (
    "⚠️ 本系统为数字孪生仿真回放，基于公开历史数据与参数化建模，非实时预测系统。"
    "决策建议需经人工确认后执行（人机协同）。"
)

# ---------------------------------------------------------------------------
# 中文字体
# ---------------------------------------------------------------------------
def _setup_cjk_font():
    from matplotlib import font_manager

    candidates = [
        "Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS",
        "Noto Sans CJK SC", "WenQuanYi Micro Hei", "PingFang SC",
    ]
    avail = {f.name for f in font_manager.fontManager.ttflist}
    for c in candidates:
        if c in avail:
            plt.rcParams["font.sans-serif"] = [c]
            break
    plt.rcParams["axes.unicode_minus"] = False


_setup_cjk_font()

AGENT_COLORS = {"A": "#2e86de", "B": "#8e44ad", "C": "#16a085", "D": "#e67e22"}


# ---------------------------------------------------------------------------
# 核心：运行一次完整推演
# ---------------------------------------------------------------------------
def run_full_simulation(scenario_key: str, use_llm: bool) -> dict:
    sim_both = P.run_both(scenario_key)
    agents = {
        "A": agent_a.run(scenario_key, use_llm),
        "B": agent_b.run(scenario_key, use_llm),
        "C": agent_c.run(scenario_key, use_llm),
        "D": agent_d.run(scenario_key, use_llm),
    }
    proto = X.run_protocol(scenario_key, agents)
    audit = X.build_audit_log(scenario_key, sim_both, agents, proto)
    tmp = tempfile.gettempdir()
    audit_path = os.path.join(tmp, f"audit_log_{scenario_key}.json")
    X.save_audit_log(audit_path, audit)
    return {
        "scenario_key": scenario_key,
        "sim_both": sim_both,
        "agents": agents,
        "protocol": proto,
        "audit": audit,
        "audit_path": audit_path,
    }


# ---------------------------------------------------------------------------
# 渲染：场景简介
# ---------------------------------------------------------------------------
def render_scenario_info(scenario_key: str) -> str:
    sc = T.get_scenario(scenario_key)
    topo = N.topology_summary()
    facts_md = "\n".join(f"- {f}" for f in sc.facts)
    topo_md = (
        f"**数字孪生拓扑**：东极微电网节点 {topo.get('dongji_buses', 0)} 个 / 电源 {topo.get('dongji_gens', 0)} 台 / "
        f"负荷 {topo.get('dongji_loads', 0)} 路；对照模型 IEEE 33 节点（母线 {topo.get('ieee33_buses', 0)} / "
        f"线路 {topo.get('ieee33_lines', 0)}）。pandapower 可用：{topo.get('pandapower_available')}"
    )
    return f"""
### 🌀 {sc.name} · {sc.subtitle}

{sc.summary}

**灾情事实（带来源）**
{facts_md}

**数据来源与简化方法（data_provenance）**
> {sc.data_provenance}

{topo_md}
"""


# ---------------------------------------------------------------------------
# 渲染：四 Agent 卡片
# ---------------------------------------------------------------------------
def render_agent_cards(agents: dict) -> str:
    cards = []
    for letter in ["A", "B", "C", "D"]:
        a = agents[letter]
        color = AGENT_COLORS[letter]
        conf = a.get("confidence", 0.0)
        conf_pct = int(conf * 100)
        findings = "".join(f"<li>{f}</li>" for f in a.get("key_findings", []))
        recs = "".join(f"<li>{r}</li>" for r in a.get("recommendations", []))
        llm_tag = "🤖 LLM" if a.get("llm_enhanced") else "⚙️ 规则"
        if a.get("fallback_reason"):
            llm_tag = "⚙️ 规则(回退)"
        cards.append(f"""
        <div style="border-left:6px solid {color};background:#f8fafc;border-radius:10px;padding:14px;margin:8px;flex:1;min-width:260px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b style="color:{color};font-size:16px">Agent {letter} · {a.get('name','')}</b>
            <span style="font-size:12px;background:#eef2f7;padding:2px 8px;border-radius:10px">{llm_tag}</span>
          </div>
          <div style="margin:6px 0;font-size:13px;color:#555">{a.get('status','')}</div>
          <div style="font-size:12px;margin:4px 0">置信度 <b>{conf_pct}%</b>
            <div style="background:#e0e0e0;border-radius:6px;height:8px;margin-top:3px">
              <div style="background:{color};height:8px;width:{conf_pct}%;border-radius:6px"></div>
            </div>
          </div>
          <div style="font-size:13px;margin-top:8px"><b>关键发现</b><ul style="margin:4px 0;padding-left:18px">{findings}</ul></div>
          <div style="font-size:13px"><b>建议</b><ul style="margin:4px 0;padding-left:18px">{recs}</ul></div>
        </div>
        """)
    return f"""<div style="display:flex;flex-wrap:wrap">{''.join(cards)}</div>"""


# ---------------------------------------------------------------------------
# 渲染：双交叉验证流程
# ---------------------------------------------------------------------------
def render_crossval(proto: dict) -> str:
    r1 = proto["round1"]
    r2 = proto.get("round2")
    final = proto["final_decision"]

    r1_color = "#e74c3c" if r1["verdict"] == "冲突" else "#27ae60"
    html = f"""
    <div style="display:flex;align-items:stretch;gap:10px;flex-wrap:wrap;margin:10px 0">
      <div style="border:2px solid {r1_color};border-radius:10px;padding:12px;min-width:240px;background:#fff">
        <b>第一轮 · A×B 互检</b><br>
        <span style="font-size:22px;color:{r1_color};font-weight:bold">{r1['verdict']}</span><br>
        <span style="font-size:12px;color:#555">峰值 {r1['peak_wind']} m/s · 海缆失效概率 {r1['cable_failure_prob']*100:.0f}%</span>
        <div style="font-size:12px;margin-top:6px">{r1['arbitration']}</div>
      </div>
    """
    if r2:
        r2_color = "#e74c3c" if r2["verdict"] == "冲突" else "#27ae60"
        esc = "🔴 转人工" if r2.get("escalate_to_human") else "🟢 一致决策"
        arrow = '<div style="font-size:28px;align-self:center">➜</div>'
        html += f"""{arrow}
      <div style="border:2px solid {r2_color};border-radius:10px;padding:12px;min-width:240px;background:#fff">
        <b>第二轮 · C×D 仲裁</b><br>
        <span style="font-size:22px;color:{r2_color};font-weight:bold">{r2['verdict']}</span>
        <span style="font-size:13px;margin-left:6px">{esc}</span><br>
        <div style="font-size:12px;margin-top:6px">{r2['decision']}</div>
      </div>"""
    html += f"""
      <div style="border:2px solid #34495e;border-radius:10px;padding:12px;min-width:200px;background:#34495e;color:#fff;align-self:center">
        <b>最终决策</b><br><span style="font-size:15px">{final}</span>
      </div>
    </div>"""
    return html


# ---------------------------------------------------------------------------
# 渲染：72h 时间轴
# ---------------------------------------------------------------------------
def render_timeline(sim_both: dict) -> str:
    tl = sim_both["windhub"]["timeline"]
    if not tl:
        return "<p>本场景未触发离网事件。</p>"
    items = []
    for e in tl:
        rel = f"T{e['rel']:+d}h"
        items.append(
            f"""<div style="display:flex;gap:12px;margin:8px 0;align-items:flex-start">
                 <div style="background:#2e86de;color:#fff;border-radius:20px;padding:4px 12px;font-size:12px;white-space:nowrap">{rel}</div>
                 <div><b>{e['label']}</b> <span style="color:#888;font-size:12px">（第 {e['hour']}h）</span><br>
                 <span style="font-size:13px;color:#555">{e['note']}</span></div>
               </div>"""
        )
    return f"<div style='background:#f8fafc;border-radius:10px;padding:12px'>{''.join(items)}</div>"


# ---------------------------------------------------------------------------
# 渲染：指标对比表
# ---------------------------------------------------------------------------
def render_metrics_table(sim_both: dict) -> str:
    b = sim_both["baseline"]["metrics"]
    w = sim_both["windhub"]["metrics"]

    def ratio(n, d):
        return "—" if d == 0 else f"{n/d*100:.0f}%"

    rows = [
        ("关键负荷保供率", f"{b['critical_supply_rate']*100:.1f}%", f"{w['critical_supply_rate']*100:.1f}%", "目标 ≥90%"),
        ("总保供率", f"{b['total_supply_rate']*100:.1f}%", f"{w['total_supply_rate']*100:.1f}%", ""),
        ("柴油消耗", f"{b['diesel_liters']:.0f} L", f"{w['diesel_liters']:.0f} L", f"风枢/基线 = {ratio(w['diesel_liters'], b['diesel_liters'])} (目标 ≤60%)"),
        ("风险敞口", f"{b['risk_exposure']:.2f}", f"{w['risk_exposure']:.2f}", f"风枢/基线 = {ratio(w['risk_exposure'], b['risk_exposure'])} (目标 ≤50%)"),
        ("切负荷总量", f"{b['shed_energy_mwh']:.1f} MWh", f"{w['shed_energy_mwh']:.1f} MWh", ""),
        ("决策耗时", "分钟级（人工）", "秒级（自动）", "人机协同"),
    ]
    tr = "".join(
        f"<tr><td>{name}</td><td style='color:#c0392b'>{bv}</td><td style='color:#27ae60;font-weight:bold'>{wv}</td><td style='color:#888;font-size:12px'>{note}</td></tr>"
        for name, bv, wv, note in rows
    )
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead><tr style="background:#34495e;color:#fff"><th>指标</th><th>基线（三级规则）</th><th>风枢（预测性）</th><th>说明</th></tr></thead>
      <tbody>{tr}</tbody>
    </table>
    <p style="font-size:12px;color:#666">口径：关键负荷=医疗+淡化+通信；保供率=实际供电时数/72h；柴油消耗=Σ出力×0.25 L/kWh；
    风险敞口=Σ(设备暴露时长×该风速失效概率)。公式见 README，未篡改。</p>
    """


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------
def plot_metrics(sim_both: dict):
    b = sim_both["baseline"]["metrics"]
    w = sim_both["windhub"]["metrics"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    specs = [
        ("关键负荷保供率 (%)", b["critical_supply_rate"] * 100, w["critical_supply_rate"] * 100, "#27ae60"),
        ("柴油消耗 (L)", b["diesel_liters"], w["diesel_liters"], "#c0392b"),
        ("风险敞口", b["risk_exposure"], w["risk_exposure"], "#8e44ad"),
    ]
    for ax, (title, bv, wv, accent) in zip(axes, specs):
        ax.bar(["基线", "风枢"], [bv, wv], color=["#c0392b", "#27ae60"])
        ax.set_title(title, fontsize=12)
        for i, v in enumerate([bv, wv]):
            ax.text(i, v, f"{v:.1f}", ha="center", va="bottom", fontsize=10)
        ax.margins(y=0.18)
    fig.suptitle("基线 vs 风枢 · 关键指标对比", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig


def plot_curves(sim_both: dict):
    b_recs = sim_both["baseline"]["records"]
    w_recs = sim_both["windhub"]["records"]
    hours = [r["hour"] for r in w_recs]
    b_rate = [r["critical_supplied"] / r["critical_demand"] if r["critical_demand"] > 0 else 1 for r in b_recs]
    w_rate = [r["critical_supplied"] / r["critical_demand"] if r["critical_demand"] > 0 else 1 for r in w_recs]
    b_shed = [r["shed_mwh"] for r in b_recs]
    w_shed = [r["shed_mwh"] for r in w_recs]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 6))
    ax1.plot(hours, b_rate, label="基线", color="#c0392b", lw=1.6)
    ax1.plot(hours, w_rate, label="风枢", color="#27ae60", lw=1.6)
    ax1.set_title("关键负荷供电率 (72h)", fontsize=12)
    ax1.set_ylim(0, 1.05)
    ax1.set_xlabel("小时"); ax1.set_ylabel("供电率")
    ax1.legend(); ax1.grid(alpha=0.3)

    ax2.plot(hours, b_shed, label="基线", color="#c0392b", lw=1.6)
    ax2.plot(hours, w_shed, label="风枢", color="#27ae60", lw=1.6)
    ax2.set_title("切负荷量 (72h, MWh/h)", fontsize=12)
    ax2.set_xlabel("小时"); ax2.set_ylabel("MWh")
    ax2.legend(); ax2.grid(alpha=0.3)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Gradio 应用
# ---------------------------------------------------------------------------
def build_app():
    scenario_choices = [(T.get_scenario(k).name, k) for k in T.get_scenario_keys()]

    with gr.Blocks(title="风枢·群岛 WindHub Islands") as demo:
        gr.Markdown(
            f"# 🌀 风枢·群岛 WindHub Islands\n"
            f"### 东极岛台风 72 小时沙盘推演看板 · 海岛韧性微电网多智能体预测性协同调度系统（AI4Climate）\n"
            f"<span style='font-size:13px;background:#eee;padding:4px 10px;border-radius:10px'>{MODE_BADGE}</span>"
        )

        scenario_state = gr.State(None)

        with gr.Tabs():
            # ---------- Tab1 选台风 ----------
            with gr.Tab("① 选台风"):
                scenario_dd = gr.Dropdown(
                    choices=scenario_choices, value=scenario_choices[0][1],
                    label="选择台风场景", interactive=True,
                )
                scenario_info = gr.Markdown(render_scenario_info(scenario_choices[0][1]))
                run_btn = gr.Button("▶ 开始 72 小时推演", variant="primary")
                scenario_dd.change(
                    fn=render_scenario_info, inputs=scenario_dd, outputs=scenario_info
                )

            # ---------- Tab2 看协商 ----------
            with gr.Tab("② 看协商"):
                agent_cards = gr.HTML(label="四 Agent 协商卡片")
                gr.Markdown("### 🔁 双交叉验证协议（可信 AI）")
                crossval_html = gr.HTML(label="交叉验证流程")
                gr.Markdown("### ⏱ 72h 关键决策时间轴")
                timeline_html = gr.HTML(label="时间轴")

            # ---------- Tab3 比结果 ----------
            with gr.Tab("③ 比结果"):
                gr.Markdown("### 📊 指标双柱对比（基线 vs 风枢）")
                metrics_plot = gr.Plot(label="指标对比图")
                metrics_table = gr.HTML(label="指标对比表")
                gr.Markdown("### 📈 72h 曲线（切负荷 + 关键负荷供电）")
                curves_plot = gr.Plot(label="72h 曲线")
                gr.Markdown("### 📥 审计日志（逐小时决策链）")
                audit_file = gr.File(label="下载 audit_log.json")
                audit_note = gr.Markdown()

        gr.Markdown(f"---\n<p style='text-align:center;color:#c0392b;font-size:13px'>{FOOTER}</p>")

        def on_run(scenario_key):
            res = run_full_simulation(scenario_key, USE_LLM)
            return (
                res,
                render_agent_cards(res["agents"]),
                render_crossval(res["protocol"]),
                render_timeline(res["sim_both"]),
                plot_metrics(res["sim_both"]),
                render_metrics_table(res["sim_both"]),
                plot_curves(res["sim_both"]),
                res["audit_path"],
                f"✅ 已生成 {T.get_scenario(scenario_key).name} 的逐小时审计日志，可点击下载。",
            )

        run_btn.click(
            fn=on_run,
            inputs=scenario_dd,
            outputs=[
                scenario_state, agent_cards, crossval_html, timeline_html,
                metrics_plot, metrics_table, curves_plot, audit_file, audit_note,
            ],
        )

    return demo


if __name__ == "__main__":
    demo = build_app()
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False, theme=gr.themes.Default())
