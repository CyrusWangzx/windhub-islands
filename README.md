# 🌀 风枢·群岛 WindHub Islands

> **一句话定位**：面向台风极端天气的「东极海岛韧性微电网多智能体预测性协同调度」数字孪生沙盘，四个 AI Agent 通过双交叉验证协议，在台风来临前预充储能、提前收桨、动态保关键负荷，把关键负荷保供率从基线 ~70% 提升至 ~100%，同时大幅降低柴油消耗与设备风险敞口。

**参赛**：AI4Climate · 评审将打开链接实际操作。

---

## 一、三步操作指南（评审必读）

1. **克隆并安装**
   ```bash
   git clone <repo>
   cd <repo>
   pip install -r requirements.txt
   ```
2. **一键启动**（无需任何 API Key 即可完整演示）
   ```bash
   python app.py
   ```
   浏览器打开 `http://127.0.0.1:7860` ，右上角显示「🟡 规则引擎模式（未配置 API）」。
3. **开始推演**：在「① 选台风」选择场景（烟花/灿鸿/米娜/兰屿级）→ 点击「▶ 开始 72 小时推演」→ 在「② 看协商」「③ 比结果」查看四 Agent 协商、双交叉验证与指标对比。

> 可选增强：复制 `.env.example` 为 `.env` 并填入 `LLM_API_KEY` 等，重启后右上角变为「🟢 LLM 增强模式」，四 Agent 输出由 LLM 生成（规则引擎结果作为 grounding 传入）。断网或解析失败自动回退规则引擎，不报错。

---

## 二、本地运行 / 部署

### 本地运行
```bash
pip install -r requirements.txt
python app.py
```
- 全程无 LLM API Key 也能完整演示（内置规则引擎降级）。
- 不依赖任何运行时联网下载；所有台风数据内置并标注来源。

### Hugging Face Spaces（主部署，公网可访问）
1. 在 HF Spaces 新建 Space，选用 **Gradio** 模板。
2. 上传全部文件（`app.py`、`requirements.txt`、`src/` 等），`requirements.txt` 会被自动安装。
3. HF 会自动执行 `python app.py` 并暴露 Gradio 服务。**无需 Docker、无需数据库、无付费云服务。**

### Vercel / 其他备选
- 本系统为纯 Python 单进程 Gradio 应用，不适合无服务架构（Vercel Functions），建议以容器/长驻进程方式部署（如 Render、Railway、Fly.io），构建命令 `pip install -r requirements.txt`，启动命令 `python app.py`。

---

## 三、数据来源与 AI 使用说明

### 3.1 台风数据来源（内置硬编码，`src/data/typhoons.py`）
| 场景 | 类型 | 来源 | 简化方法 |
|---|---|---|---|
| 烟花 (2021) | 真实回放（主场景） | 舟山市人民政府 / 应急管理局灾情通报（全市直接经济损失 16.32 亿元，累计 17.62 万户次停电）；中央气象台（CMA）路径集 | 以公开影响时间线为锚，风速余弦平滑插值、距离/气压线性插值 |
| 灿鸿 (2015) | 真实（超强台风） | 中央气象台（CMA）台风最佳路径数据集（CMA-STI） | 以最佳路径强度锚点逐小时平滑插值 |
| 米娜 (2019) | 真实（强台风） | 中央气象台（CMA-STI） | 同上 |
| 兰屿级（合成极端） | **合成外推** | 2023 台风「小犬」台湾兰屿实测阵风 95.2 m/s（公开媒体/气象观测） | 以该实测阵风为强度上限做参数化外推，**明确标注为合成压力测试数据** |

**纪律**：禁止编造未标注来源的数据；所有序列均经 `data_provenance` 字段注明来源与简化方法；本系统为**数字孪生仿真回放，非实时预测**。

### 3.2 AI 使用方式与降级机制
- **四 Agent 层**（`src/agents/`）：气象态势感知(A)、设备健康与风险(B)、能源调度优化(C)、高空风能管控(D)。统一输出结构化 JSON（`agent/status/confidence/key_findings/recommendations/reasoning_trace`）。
- **规则引擎模式（默认，无 Key 必须 work）**：基于风速时序 + 设备阈值的确定性决策（收桨 28m/s、海缆风险=f(风速持续时长)、储能按 SOC 分段），输出专业可信中文分析。
- **LLM 增强模式**：`.env` 配置 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` 后，四 Agent 各自调用 OpenAI 兼容 `chat.completions`，规则引擎输出作为 grounding 传入 system prompt；LLM 输出必须可解析 JSON，解析失败自动回退规则引擎结果并记录。**禁止在无 Key 时伪装 LLM 调用**（模式徽标如实显示）。
- **人机协同**：所有决策建议均标注「需经人工确认后执行」，极端工况（如兰屿级）由双交叉验证第二轮标记 `escalate_to_human=true`（转人工）。

### 3.3 双交叉验证协议（可信 AI 核心，已可视化）
- **第一轮** `crosscheck(A,B)`：气象预测(A) × 设备风险(B) 互检。A 预报强风 且 B 判定设备高失效风险 → 判定「冲突」→ 自动触发第二轮；A 强风但 B 判定可承受 → 「一致 / 维持运行」。
- **第二轮** `align(C,D)`：调度策略(C) × 设备控制(D) 对齐仲裁。一致 → 输出一致决策；不一致 → `escalate_to_human=true`。
- 全程决策链落盘 `audit_log.json`（逐小时）。

---

## 四、指标口径与校准说明（诚实口径，公式不篡改）

### 4.1 关键负荷定义
**关键负荷 = 医疗 + 海水淡化 + 通信基站**（最高优先级三类）。

### 4.2 指标公式
- **保供率** = 关键负荷（医疗+淡化+通信）实际供电时数 / 72h
  $$
  R_{supply} = \frac{\sum_{h=0}^{71} E_{crit,supplied}(h)}{\sum_{h=0}^{71} E_{crit,demand}(h)}
  $$
- **柴油消耗** = Σ 柴发出力(MW) × 1h × 1000 × 0.25 L/kWh
  $$
  L_{diesel} = \sum_h P_{diesel}(h) \times 1000 \times 0.25
  $$
- **风险敞口** = Σ_h Σ_e [设备 e 在 h 时刻暴露于超阈值风速的时长(1h)] × [该设备该风速下失效概率]
  $$
  Risk = \sum_{h}\sum_{e} \mathbb{1}_{exposed}(e,h) \cdot P_{fail}(e, v(h))
  $$
  失效概率曲线见 `src/sim/metrics.py` 的 `EQUIPMENT_MODELS`（海缆阈值 25m/s、系留风机 28m/s、光伏 30m/s 等，透明可调）。

### 4.3 校准目标与达成（烟花场景）
通过**调整策略参数（非篡改指标计算）**达成：

| 指标 | 基线（三级规则） | 风枢（预测性） | 目标 | 达成 |
|---|---|---|---|---|
| 关键负荷保供率 | 70.8% | 100% | 风枢 ≥90% | ✅ |
| 柴油消耗 | 11000 L | 2767 L | 风枢 ≤ 基线 60% | ✅ (25%) |
| 风险敞口 | 11.73 | 1.10 | 风枢 ≤ 基线 50% | ✅ (9%) |
| 基线保供率 | 70.8% | — | 基线 65–75%（未故意弱化） | ✅ |

**参数依据（`src/sim/policies.py`，透明建模假设）**：
- 柴发带有限燃油预算 `DIESEL_BUDGET_MWH=44`（现实燃料约束，两策略共用、公平）。
- **基线（三级规则）**：不预充储能（初始 30% SOC）、负荷优先级静态、把柴油用于覆盖**全部**负荷（含非关键）→ 燃料很快耗尽 → 后期关键负荷失电。
- **风枢（预测性）**：台风前利用主网把储能**预充至满**（2MWh）；动态负荷优先级（优先保关键、切除非关键）；高空系留风机在安全窗口(8–28m/s)增发、**>28m/s 收桨（零暴露）**；柴发**延迟至 SOC<30% 且可再生不足**时才启动、仅补足关键负荷缺口 → 油耗大幅降低、关键负荷全程保供。
- **海缆离网**：基线被动硬故障（>25m/s 持续 3h）；风枢主动预测性解列（>20m/s 即受控离网），海缆在风暴核心前退出运行 → 暴露与失效风险显著降低。

> 说明：校准带（基线 65–75%）在烟花/灿鸿/米娜三个真实场景中均成立；**兰屿级**为合成极端压力测试，其基线在超设计标准工况下自然退化（<65%），用以展示极端韧性边界与系统转人工价值，属物理诚实，非故意弱化。

---

## 五、技术栈与仓库结构

### 技术栈（硬约束）
| 组件 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.10+ | 单仓库 |
| 电网仿真 | **pandapower** | 东极拓扑建模 + IEEE 33 节点标准对照模型（一并内置） |
| 前端 | **Gradio Blocks** | 三 Tab + 顶部场景选择器，中文界面 |
| Agent 层 | LLM API（OpenAI 兼容）×4 + 规则引擎降级 | 见上文 |
| 数据 | 内置 Python 数据模块 | 不依赖运行时联网下载 |
| 部署 | Hugging Face Spaces 为主 | README 附本地/Vercel 备选，公网可访问 |

**禁止**：联网实时数据源、付费云服务、引入数据库（全部内存/文件级）。

### 仓库结构
```
.
├── app.py                      # Gradio 前端看板入口
├── requirements.txt            # pandapower/gradio/matplotlib/pandas/requests/python-dotenv
├── .env.example                # 可选 LLM 配置模板
├── README.md                   # 本文件（评审必读）
├── audit_log.json              # 示例输出（烟花场景）
└── src/
    ├── data/typhoons.py        # 4 个场景 72h 时序 + data_provenance
    ├── sim/
    │   ├── network.py          # pandapower 东极拓扑 + IEEE33 + 潮流校验
    │   ├── policies.py         # baseline_policy / windhub_policy，72h 仿真
    │   └── metrics.py          # 指标口径与风险敞口公式
    ├── agents/
    │   ├── agent_a.py ~ agent_d.py   # 四 Agent 封装
    │   ├── rule_engine.py      # 规则引擎（默认）
    │   └── llm_client.py       # LLM 增强 + 回退
    └── crossval/protocol.py    # 双交叉验证 + 审计日志
```

---

## 六、交付物清单
- ✅ `app.py`、`requirements.txt`、`.env.example`
- ✅ `src/data/typhoons.py`
- ✅ `src/agents/{agent_a,agent_b,agent_c,agent_d,rule_engine,llm_client}.py`
- ✅ `src/crossval/protocol.py`
- ✅ `src/sim/{network,policies,metrics}.py`
- ✅ `audit_log.json`（示例输出）、`README.md`

## 七、验收标准对照
- ✅ 全新环境 `pip install -r requirements.txt && python app.py` 启动成功，无 API Key 全流程可跑。
- ✅ 4 个场景推演均完成；双交叉验证在烟花、兰屿场景出现「冲突→第二轮仲裁」路径（米娜展示「维持运行」路径）。
- ✅ Tab3 指标达成 M5 校准要求（见第四节），页面标注仿真口径。
- ✅ 配置 LLM Key 后切「LLM 增强模式」，四 Agent 输出为 LLM 生成且结构化可解析；断网/解析失败自动降级不报错。
- ✅ 中文界面无乱码；页面含数字孪生口径声明与人机协同声明（固定页脚）。
- ✅ 禁止事项全部遵守：数据内置且标注来源；模式徽标如实；指标公式未篡改；无商业付费依赖。

---

## 八、许可证

本项目以 [MIT License](./LICENSE) 开源。
台风数据来源与简化方法见第三节，仅用于研究与教学演示；决策建议需经人工确认后执行。
