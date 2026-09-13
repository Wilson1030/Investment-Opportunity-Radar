# Investment Opportunity Radar

> 投资机会雷达 —— 以投资者画像为核心、以事件为入口、以投资 Thesis 为组织方式、以证据链为可信基础、以 AI 为研究辅助能力的**个性化投资机会发现系统**。

**本项目不回答「你想看哪只股票」，而回答「根据你的投资逻辑，市场上现在发生了哪些值得你关注的事情」。**

---

## 🚀 快速开始（一条命令）

```bash
# 前置：conda 环境 radar 已建好、frontend 已 npm install、数据库已初始化
python tools/reset_db.py --yes --seed      # 首次：建库（--seed 造一组离线样例）
python tools/start_all.py                  # 启动后端 + 前端，Ctrl+C 一起停
```

打开 **http://127.0.0.1:5173** 即可。启动前会自动检查解释器依赖 / 端口占用 /
前端依赖 / 数据库文件，并给出可执行的修复提示，而不是让你对着报错猜。

想快速接手代码：先看 **[docs/09-项目状态与交接.md](docs/09-项目状态与交接.md)** ——
一句话状态、已验证的事实与数值、已修 bug 及其教训、代码地图。

想换模型：**[docs/08-模型接入指南.md](docs/08-模型接入指南.md)** ——
13 个 provider 预设，**只填 key 就能用**。

---

## ⚠️ 定位与免责

- 本系统是**投资研究与信息组织工具**，不是行情软件，不是选股器，不是荐股工具。
- 系统中的一切评分、匹配度、AI 判断均为**概率性研究线索**，不构成投资建议，不承诺任何收益。
- AI 输出**不使用确定性语言**；所有重要判断必须可追溯至证据（公告 / 财报 / 可信媒体 / 机构观点 / 市场讨论）。
- 每个投资逻辑（Thesis）都必须定义**失效条件**。
- Owner 自用系统，**本地单用户**，不做 SaaS。

---

## 核心链路

```
Investor → InvestmentProfile → 信息与事件 → 事件理解 → 投资逻辑识别
   → 候选标的 → 个性化机会匹配 → 证据链 → 待确认 → 重点跟踪
   → 逻辑成立 / 逻辑失效 → 归档
```

**核心对象不是 `Stock`，而是**
`Investor · InvestmentProfile · Company · Event · Evidence · Thesis · Opportunity · OpportunityStatus`

> 股票只是底层对象，**事件是机会入口，投资逻辑是核心，投资者画像决定排序，证据链负责解释，状态流负责持续跟踪**。

---

## 目录结构

```
Investment_Opportunity_Radar/
├── docs/                      # 设计与决策文档（先读这里）
│   ├── 00-决策记录.md          # 冻结的架构决策与理由（ADR）
│   ├── 01-产品规格拆解.md      # 原始规格 → 可执行需求条目
│   ├── 02-领域模型与数据模型.md
│   ├── 03-系统架构与AI工作流.md
│   ├── 04-评分与证据链.md
│   ├── 05-API契约.md
│   ├── 06-策略设计总表.md      # 10 类 Thesis 的统一设计
│   ├── 07-MVP实施计划.md
│   ├── 08-模型接入指南.md
│   └── 09-项目状态与交接.md
├── backend/                   # FastAPI + SQLModel + SQLite
│   └── app/
│       ├── models/            # 领域实体
│       ├── engine/            # 规则引擎（确定性）
│       ├── ai/                # LLM 节点（纯函数 JSON→JSON）
│       ├── strategies/        # 策略注册表（可插拔）
│       ├── ingest/            # 采集适配器（akshare / cninfo）
│       ├── scheduler/         # 交易日感知三段式调度
│       └── api/               # REST 接口
├── frontend/                  # Vite + React + TS + Tailwind（深色研究终端风）
├── data/
│   ├── mock/                  # 手工样例数据（可复现的边界案例）
│   ├── cache/                 # 采集缓存、LLM 节点缓存（gitignored）
│   └── raw/                   # 公告原文 PDF/HTML（gitignored）
└── .env.example
```

---

## 快速开始

### 1. 环境（独立 conda 环境，与本机其他项目隔离）

```bash
conda create -n radar python=3.13 -y
conda activate radar
pip install -e backend/
```

### 2. 配置

```bash
cp .env.example .env
```

开发期默认使用**本机 Ollama**（零 API 成本、公告内容不出本机）：

```bash
ollama pull qwen3:4b
ollama serve
```

想换云端模型？**只改 provider 名 + 填 key**，端点已内置，不用记 base_url：

```bash
LLM_MODE=hybrid              # 抽取本地、分析云端（推荐折中）
ANALYZE_PROVIDER=deepseek
ANALYZE_MODEL=deepseek-reasoner
DEEPSEEK_API_KEY=sk-xxxxxxxx

# 其余已预留：zhipu(glm) / kimi(moonshot) / dashscope(qwen) / openai
#             claude / openrouter / groq / siliconflow / minimax / custom
```

**支持的 provider 全表、成本参考、三个已知的坑** → [`docs/08-模型接入指南.md`](docs/08-模型接入指南.md)

### 3. 启动

```bash
# 后端
conda activate radar
cd backend && uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend && npm install && npm run dev
```

访问 http://localhost:5173

---

## 开发原则（源自产品规格第 60 节，开发时必须遵守）

1. 不要理解成传统股票行情软件；K 线、技术指标属于**辅助信息层**。
2. 优先围绕「事件 → Thesis → Opportunity → Evidence」设计。
3. 所有机会必须结合**用户投资倾向**；同一个市场，不同用户看到不同的雷达。
4. 所有重要 AI 判断尽可能提供**证据链**，并区分事实 / 推断 / 假设 / 市场讨论。
5. 必须展示不确定性，必须主动寻找**反证**。
6. 每个 Thesis 都必须有**失效条件**；「待确认」必须写明**具体待确认事项**。
7. 评分不可设计成不可解释的黑盒。
8. **确定性的事情交给规则与程序；需要语义理解的事情交给 LLM。**
9. 不要让 LLM 无差别分析所有股票——先建候选池。
10. 新闻需去重、聚类、事件化；财报需结构化并提炼潜在投资逻辑。
11. 首页强调「今日机会」与「为什么现在」，而不是信息堆叠。
12. 用户行为可以形成 Feedback Loop，但**必须允许用户覆盖 AI 学习结果**。

---

## 当前状态：**10 类投资策略全部实现**

| 维度 | 状态 |
|---|---|
| 设计文档 | 10 份 / 共 5400+ 行（`docs/00` ~ `docs/09`） |
| 后端 | 90+ 模块 · **616 个测试通过** |
| 策略 | **10 / 10 已实现**：重组预期 · 困境反转 · 事件驱动 · 并购整合 · 股东行为 · 政策驱动 · 行业周期 · 成长 · 技术突破 · 价值发现 |
| 数据源 | cninfo 公告 · 同花顺财务 · 新浪现金流 · **百度估值** · 财联社/新浪新闻 · 交易日历 |
| 前端 | 5 页面 · `tsc` + `vite build` + **渲染自检**通过 |
| 迁移 | 5 个 Alembic 迁移 / 29 张表 |

### 已验证的能力（不是「计划」，是已经跑通的）

```
真实数据   cninfo 全市场 → 候选池 → PDF 解析失败率 0.00 → 事件抽取 → 证据链 → 机会卡
           实测 6 个机会 63 秒（含 18 次 LLM 调用）
LLM        抽取 12/12、反证 6/6、叙事 6/6、语义分 6/6；单条约 7 秒
           13 个 provider 全预留（只填 key 即可换云端）；结构化输出约束字段
财务       同花顺 10/10 家 × 120 期；新浪现金流补上**真经营现金流总额**
估值       百度股市通 4/4 家：市值 / PE / PB + **近三年分位**（低估判定有据可依）
新闻       财联社 + 新浪：去重 → 按公司/事件聚类 → 要点取真实标题（可核对）
策略       10 类全部实现，共享层承载机制、各策略只声明差异
           每类策略都有 docs/06 §16 指定的**验收闸门**测试
可解释     每个判断可追溯到证据；财务信号逐条对应评分规则键
安全阀     确定性语言守卫 · 证据闸门 · 12 条领域不变量 · 标签不得决定策略
```

复现：`cd backend && python -m pytest`（616 通过）·
`cd frontend && npm run render:check`（真实渲染自检）

详见 `docs/07-MVP实施计划.md`（实施记录）与 `docs/09-项目状态与交接.md`（接手要点）。
