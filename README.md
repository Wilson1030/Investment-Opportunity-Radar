# Investment Opportunity Radar

> 投资机会雷达 —— 以投资者画像为核心、以事件为入口、以投资 Thesis 为组织方式、以证据链为可信基础、以 AI 为研究辅助能力的**个性化投资机会发现系统**。

**本项目不回答「你想看哪只股票」，而回答「根据你的投资逻辑，市场上现在发生了哪些值得你关注的事情」。**

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
│   └── 07-MVP实施计划.md
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

开发期默认使用**本机 Ollama**（零 API 成本）：

```bash
ollama pull qwen3:4b
ollama serve
```

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

## 当前状态

| 阶段 | 内容 | 状态 |
|:---:|---|:---:|
| 0 | 决策冻结（13 项） | ✅ |
| 1 | 目录 / git / conda 环境 | ✅ |
| 2 | 设计文档 8 份 | ✅ |
| 3 | 后端骨架 + 建表 + 测试 | ✅ 234 个测试通过 |
| 4 | 前端骨架 + 5 页面 | ✅ 类型检查 + 生产构建通过 |
| 5 | 采集脚本 + mock 数据 | ✅ 含规格 §5.6 的 10 个示例 |
| 6 | 重组预期闭环（真实数据） | ⬜ 下一步 |


### 已验证的能力（不是「计划」，是已经跑通的）

```
后端   234 个测试通过（含 12 条领域不变量、评分算例、证据闸门、状态机、幂等性）
数据库 26 张业务表由 Alembic 管理（alembic upgrade head 成功）
API    /api/health 返回 ok（26 张表 + 10 类策略注册表自检通过）
前端   tsc --noEmit 与 vite build 均通过；后端未启动时自动降级为「离线演示数据」
LLM    真实 Ollama qwen3:4b 抽取一次通过（105s/条，JSON 合规，证据片段未被改写）
门控   规格 §5.6 示例 E / I 的顺序门控在**机制层**锁定（不是靠 prompt 提醒）
```

复现命令：`cd backend && python -m pytest`、`python ../tools/smoke_ollama.py`

详见 `docs/07-MVP实施计划.md`。
