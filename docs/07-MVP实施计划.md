# 07 · MVP 实施计划

> 阶段划分、验收标准、风险检查点、待决策事项。
> 每阶段结束必须通过验收闸门才能进入下一阶段。

---

## 1. 阶段总览

| 阶段 | 内容 | 状态 | 验收闸门 |
|:---:|---|:---:|---|
| P0 | 决策冻结（13 项） | ✅ 完成 | `docs/00` 已确认 |
| P1 | 目录 / git / conda 环境 | ✅ 完成 | `conda env list` 含 `radar`；`git status` 正常 |
| P2 | 设计文档 8 份 | ✅ 完成 | 文档互相引用无断链；`06` 覆盖 10 类 Thesis |
| P3 | 后端骨架 + 建表 + 测试 | 🔄 进行中 | `pytest` 全绿；`alembic upgrade head` 成功；`/api/health` 返回 ok |
| P4 | 前端骨架 + 5 页面 + 深色主题 | ⬜ | `npm run dev` 可开；5 个路由可访问；主题变量集中可换肤 |
| P5 | 采集脚本 + mock 数据 | ⬜ | `python -m app.ingest --dry-run` 可跑；mock 数据可生成机会卡 |
| **P6** | **重组预期闭环（真实数据）** | ⬜ | 见 §4 |
| P7 | dry-run 预热 3 个交易日 | ⬜ | 见 §5 |
| P8 | 前端接真实 API | ⬜ | Radar 首页显示真实机会卡 |
| P9 | 调度启用（真写） | ⬜ | 见 §6 |
| P10 | 策略逐个扩展（2~10） | ⬜ | 每个策略通过 §`06`-16 的 6 项闸门 |

---

## 2. P3 后端骨架 · 验收清单

### 2.1 交付物

```
backend/
├── pyproject.toml
├── alembic.ini
├── alembic/versions/0001_initial.py
├── app/
│   ├── config.py          # .env → Settings
│   ├── db.py              # engine / session / get_session
│   ├── main.py            # FastAPI 装配 + CORS(127.0.0.1) + 启动自检
│   ├── models/            # 02 文档的全部实体 + enums
│   ├── ingest/
│   │   ├── base.py        # DataSourceAdapter 抽象
│   │   ├── cninfo.py      # 公告列表 + 全文 + PDF 段落切分
│   │   ├── akshare_source.py
│   │   ├── normalizer.py  # 清洗 + 幂等去重
│   │   └── cli.py         # python -m app.ingest
│   ├── engine/
│   │   ├── scope.py       # 候选池筛选（st_and_risk_warning）
│   │   ├── classifier.py  # 公告类型 → EventType 白名单
│   │   ├── rules.py       # 规则命中
│   │   ├── scoring.py     # 规则分 + ScoreItem 拆解
│   │   ├── freshness.py   # 时效衰减
│   │   ├── funnel.py      # 漏斗编排 + 计数
│   │   └── guard.py       # 禁用词 / 证据闸门 / 不变量校验
│   ├── ai/
│   │   ├── provider.py    # LLMProvider + Ollama/DeepSeek/OpenAI 实现
│   │   ├── cache.py       # 基于 llm_node_run 的缓存
│   │   ├── runner.py      # 执行器：校验 / 重试 / 缓存 / 审计
│   │   ├── schemas.py     # 节点 I/O Pydantic 模型
│   │   ├── prompts/       # 版本化 prompt
│   │   └── nodes/         # 5 个纯函数节点
│   ├── strategies/
│   │   ├── base.py        # Strategy 协议
│   │   ├── registry.py    # 10 类注册（1 implemented + 9 designed）
│   │   └── restructuring/ # rules / thesis / invalidation / weights / questions
│   ├── scheduler/
│   │   ├── calendar.py    # 交易日历（含退化）
│   │   └── jobs.py        # 三段式
│   └── api/               # 按 05-API契约 实现（骨架期可有 mock 数据）
└── tests/
```

### 2.2 必须通过的测试

| 测试 | 内容 | 关联 |
|---|---|---|
| `test_invariants.py` | 12 条不变量逐条验证（`02` §5） | INV-* |
| `test_scoring.py` | `04` §8 的算例必须复算出 `rule_score = 67.5875`（显示 68） | M6-03 |
| `test_match_score.py` | §5.8 三组示例必须复现 `55 / 94 / 18` | M5-08 |
| `test_registry_selfcheck.py` | 10 类策略失效条件非空（INV-TT1） | §58-5 |
| `test_no_tag_mapping.py` | 扫描规则源码，禁止 `match_by_tag` 类接口（INV-C1、§06-14.1） | §5.6 示例 J |
| `test_coverage_gating.py` | `policy` C5 未命中 → coverage ≤ 0.45；`product` C3 未命中 → C4/C5 = 0 | §06-14.2 |
| `test_evidence_gate.py` | 伪造 `relevant_text` 必须被拒（子串校验） | M4-01/02 |
| `test_ban_words.py` | 禁用词扫描能命中并触发重生成 | M11-02 |
| `test_status_machine.py` | 非法状态迁移 → 409 | M7-01 |
| `test_idempotent.py` | 同一批数据采集两次，行数不变 | M12-04 |
| `test_api_contract.py` | 响应结构快照 + `disclaimer` 必存在 | `05` §10 |

### 2.3 启动自检（`main.py` 启动时执行）

```python
def startup_selfcheck():
    assert all_defs_have_invalidating_events()      # INV-TT1
    assert all_implemented_have_rules()             # INV-TT2
    assert weights_sum_to_0_95()                    # 04 §3
    assert no_tag_mapping_in_rules()                # INV-C1
    assert trading_calendar_reachable() or warn(...)  # R5
```

---

## 3. P4 前端骨架 · 验收清单

### 3.1 交付物

```
frontend/
├── package.json  vite.config.ts  tsconfig.json
├── tailwind.config.ts   # 主题变量集中处（D05）
├── index.html
└── src/
    ├── main.tsx  App.tsx  router.tsx
    ├── styles/theme.css      # 底色/面板/边框/主文/次文/强调/状态色
    ├── api/{client.ts,types.ts}
    ├── components/
    │   ├── OpportunityCard.tsx
    │   ├── ScoreBreakdown/     # 两级展开：维度 → ScoreItem
    │   ├── EvidenceDrawer.tsx  # 证据原文段落 + 定位
    │   ├── Timeline.tsx        # 事件时间线
    │   ├── StatusBadge.tsx     # 7 种状态
    │   ├── ReliabilityTag.tsx  # A~E 级标记
    │   ├── FreshnessTag.tsx    # New/Updated/Breaking/Stale
    │   ├── DivergenceBadge.tsx # 规则/语义分歧
    │   └── DisclaimerBanner.tsx
    └── pages/
        ├── RadarPage.tsx        # M10-01
        ├── OpportunityPage.tsx  # M10-04
        ├── MyThesisPage.tsx     # M10-05
        ├── EventsPage.tsx       # M10-06
        └── ProfilePage.tsx      # M10-07
```

### 3.2 视觉规范（D05，`tailwind.config.ts` 集中管理）

```javascript
colors: {
  bg:      '#0B0E11',   panel:   '#12161C',   border:  '#1F262E',
  text:    '#E6E9EF',   muted:   '#8B94A3',   accent:  '#2DD4BF',
  status: {
    pending:   '#F59E0B',   // 待确认
    tracking:  '#2DD4BF',   // 重点跟踪
    confirmed: '#22C55E',   // 逻辑成立
    invalid:   '#EF4444',   // 逻辑失效
    archived:  '#6B7280',
  },
}
fontFamily: { mono: ['JetBrains Mono', 'Consolas', 'monospace'] }  // 数字一律等宽
```

### 3.3 前端硬性约束

| 约束 | 检查方式 |
|---|---|
| 首屏不出现 K 线（M10-08） | 代码审查：RadarPage 不引入图表库 |
| 风险维度必须带方向标注 | 快照测试断言 `direction_note` 存在 |
| 数字用 `tabular-nums` | Tailwind class 检查 |
| 正文行高 ≥ 1.7（深色可读性） | theme.css |
| 免责声明常驻（不许隐藏） | 每个含分数的响应都渲染 `DisclaimerBanner` |
| 卡片操作按钮 ≤ 4 个（M9-01） | 组件 props 约束 |

---

## 4. P6 · 重组预期闭环 · 端到端验收

**这是第一个「真东西」**，验收标准直接对应规格 §55 的链路：

```
ST / *ST
  ↓
重大资产重组相关公告
  ↓
控股权 / 实控人变化
  ↓
资产注入
  ↓
AI 事件识别
  ↓
重组 Thesis
  ↓
用户匹配
  ↓
Opportunity Score
  ↓
待确认
  ↓
后续公告监控
  ↓
逻辑成立 / 逻辑失效
```

### 4.1 逐环验收

| # | 环节 | 验收标准 | 可自动验证 |
|:---:|---|---|:---:|
| 1 | ST 名单 | 能取到 ST/*ST 全量名单，数量合理（100~250） | ✅ |
| 2 | 公告采集 | 对候选池公司取到近 90 天公告，含重组类 | ✅ |
| 3 | 全文解析 | `parse_failure_rate` 可观测且被记录（不隐藏） | ✅ |
| 4 | 段落切分 | 每条成功解析的公告有 ≥1 个 Paragraph | ✅ |
| 5 | 事件识别 | 重组类公告产出 `Event(RESTRUCTURING)`，字段完整 | ✅ |
| 6 | 证据落库 | 每条 Event 关联 ≥1 条 A/B 类 Evidence，且 `relevant_text` 通过子串校验 | ✅ |
| 7 | Thesis 生成 | 产出含 `statement` + `why_now` + `invalidating_events` 的 Restructuring Thesis | ✅ |
| 8 | 画像匹配 | `match_score` 随画像权重变化而变（同一公司不同画像分数不同） | ✅ |
| 9 | 评分 | `rule_score` 有维度分 + 逐项拆解 + 证据绑定 | ✅ |
| 10 | 待确认 | `OpenQuestion` 非空且具体（不是「待确认」标签本身） | ✅ |
| 11 | 语义分 | `semantic_score` 生成，`rules_already_covered` 非空 | ⚠️ 需人工看 |
| 12 | 监控 | 构造「重组终止」事件 → 状态迁移到 `invalidated` + 生成 Alert | ✅ |
| 13 | UI | Radar 首页能看到机会卡；点击可展开到原文段落 | 人工 |

### 4.2 用规格示例做回归

`data/mock/` 中必须包含**从规格 §5.6 提取的 10 个示例**（示例 A ~ J 的虚构公司），
作为端到端回归用例：

```
mock/example_a.json  重组预期型   → restructuring,  期望 match ≈ 94, status = pending_confirmation
mock/example_b.json  业绩拐点型   → turnaround,     期望 evidence 含 4 项改善 + 3 项 uncertainty
mock/example_c.json  高股息型     → value,          期望事件为「组合成的股东回报改善」
mock/example_d.json  成长型       → growth
mock/example_e.json  政策驱动型   → policy,         期望 coverage 门控生效（无订单 → ≤0.45）
mock/example_f.json  并购整合型   → ma_integration, 期望 8 项调查清单进 OpenQuestion
mock/example_g.json  股东行为型   → shareholder_action, 期望 hunt_risk 强制产出反证
mock/example_h.json  行业周期型   → cycle,          期望入口是行业级
mock/example_i.json  新产品型     → product,        期望 C3 未命中 → C4/C5 计 0
mock/example_j.json  非ST困境反转 → turnaround,     期望 is_st=false 也能命中（关键回归）
```

> **`example_j` 是最重要的一条回归**：它验证「策略决定为什么被发现，而不是标签决定策略」（§5.6）。

---

## 5. P7 · dry-run 预热协议（3 个交易日）

`INGEST_DRY_RUN=true` 期间，采集、解析、LLM 调用与观测记录全部真实执行；
**只有「判断类」数据不落库**（Event / Evidence / Opportunity / Score / Alert）。
原始数据（Company / Announcement / Paragraph）与 `IngestRun` 照常写入 —— 详见 docs/03 §7.2。

### 5.1 每日检查项

| 指标 | 期望 | 不达标时 |
|---|---|---|
| `candidates`（候选池） | 200 ~ 400 | 检查 scope 规则 |
| `announcements_fetched` | 30 ~ 80 | 检查 cninfo 接口与分页 |
| `passed_prefilter / fetched` | 0.5 ~ 0.85 | 调整关键词白名单 |
| `parse_failure_rate` | < 0.15 | 检查 PDF 解析；标记 needs_ocr（R2） |
| `field_missing_rate` | < 0.30 | 检查 akshare 字段映射 |
| `llm_schema_failure_rate` | **< 0.20** | ★ 这是 R3 的判据，见 §5.2 |
| `evidence_rejected` | 记录并逐条归因 | 若高 → 检查 prompt 与段落切分粒度 |
| `cards` | 3 ~ 15 | 若为 0 → 看漏斗哪一级掉了 |
| 总耗时 | < 6 小时（本机 4B） | 降采样或切云端抽取 |

### 5.2 ★ R3 判据：`qwen3:4b` 能否胜任？

干预期第 3 天做判定：

```
if llm_schema_failure_rate < 0.05  → 继续用本地模型（零成本）
elif < 0.20                        → 优化 prompt（加 JSON Schema 示例 + few-shot）后再观察
else                               → 切换 EXTRACT_PROVIDER=deepseek（改 2 行 .env）
```

同时记录 `evidence_rejected` 的**性质**：
- 若大量「文本子串不匹配」→ 模型在改写原文（严重，必须换模型或改 prompt）
- 若大量「段落不存在」→ 模型在编造页码（严重）
- 若大量「无证据输出」→ 模型在无根据判断（严重）

> **这三种都属于「模型不可用于抽取」的信号**，比 JSON 不合规更危险，因为它产生的是
> **看起来合理但无法核对的证据**。

### 5.2.1 ★ 首次实测数据（2026-09-11，真实 Ollama，非模拟）

用 `tools/smoke_ollama.py` 在**一段合成的 ST 公司重组公告**（3 个分节、含风险提示）上
跑了一次真实 `extract_event`：

| 指标 | 实测值 | 判据 | 结论 |
|---|---|---|---|
| 模型 | `qwen3:4b`（本机 Ollama） | — | — |
| 单条耗时 | **105.3 s** | < 6h / 80 条 | ✅ 满足（80 条 ≈ 2.3 h 串行） |
| JSON 合规 | **1 次即通过**（`attempts = 1`） | schema_failure_rate < 0.05 | ✅ 优秀（0.00） |
| 证据闸门 | **3 条片段全部接受，0 条拒绝** | 无「文本不一致」 | ✅ 未改写原文 |
| `event_time` | `null`（公告未写明事件发生时间） | 未披露时必须为 null | ✅ 未编造时间 |
| `not_mentioned` | 自动列出「交易价格 / 审计评估结果 / 最终获批时间 / 完成时间」 | 必须存在 | ✅ 防补全生效 |
| `affected_thesis` | `[restructuring, turnaround]` | — | ✅ 合理 |

**同时发现一处真实的设计分歧（值得记录）**：

```
规则层（关键词白名单）归类：RESTRUCTURING
LLM 层归类：                M&A
```

原因：标题含「重大资产重组」→ 规则层判为 `RESTRUCTURING`；但正文主体是
「发行股份及支付现金购买资产」→ LLM 判为 `M&A`。**两个归类都不算错**，
而且 `M&A` 同样在 `restructuring` 策略的 `support_event_types` 里，因此机会仍会被发现。

**处理方式**：规则层只用于**预筛**（决定是否送进 LLM），落库的 `event_type` 以 LLM 为准。
这正好印证了 docs/03 §2.1 的职责划分 —— 规则负责高效过滤，语义归 LLM。
后续若分歧率升高，应把该指标纳入 `/api/admin/llm-runs` 的可观测项。

#### 5.2.2 ★ 真实公告实测（2026-09-11，cninfo 全市场 + 真实 Ollama）

用真实数据跑了完整链路（`--source cninfo --pool market --dry-run`）：

| 阶段 | 实测值 | 规格 §27 的估算 | 判断 |
|---|---|---|---|
| 全市场公告（6 天，14 页 × 30） | **420 条** | 1000~2000 条/天 | ✅ 同量级（14 页未取满） |
| 关键词预筛命中 | **73 条（17.4%）** | — | ✅ 预筛有效 |
| 候选公司（去重） | 2 家 | ~300 只 | ⚠ 窗口太短；需 90 天回补 |
| 全文 PDF 解析 | 4 条，**失败 0 条**（失败率 0.00） | — | ✅ **R2 比预想轻得多** |
| LLM 抽取 | **2 次调用，2 次通过** | — | ✅ |
| `llm_schema_failure_rate` | **0.00** | < 0.05 为优秀 | ✅ |
| 证据闸门 | 3 片接受，**0 片拒绝** | — | ✅ 未改写原文、未编造页码 |
| 单条 LLM 耗时 | **约 60~70 秒**（截断后） | — | ✅ 80 条 ≈ 80~90 分钟，可夜间跑完 |
| 总耗时 | 194 秒（含采集 + 解析 + 2 次 LLM） | < 6 小时 | ✅ |

##### 三个真实发现

**① 规则层的「关键词白名单」会误命中，而 LLM 纠正了它（分工正确）**

```
规则层判定：EARNINGS_TURNAROUND   ← 标题含「中报业绩说明会」
LLM 判定：  OTHER                  ← 「投资者集体接待日」不是业绩拐点
```

这正是 docs/03 §2.1 期望的分工：**规则负责廉价地过量召回，语义判断交给 LLM**。
误命中的代价只是一次 LLM 调用，不会污染数据。

**② 但 LLM 也会过度归类 —— 这是必须人工抽样的原因**

```
规则层判定：REGULATORY_RISK       ← 标题含「问询函」
LLM 判定：  RESTRUCTURING         ← 「向特定对象发行股票审核问询函回复」
```

定向增发的问询函回复**不是**重大资产重组。证据片段是真实的（通过了闸门），
但**归类偏了**。这类错误只能靠 dry-run 期的人工抽样发现 —— 因此
docs/07 §5.3 要求的「随机 20 条人工核对」不是可选项。

> 建议把「规则层 vs LLM 层的分类分歧率」纳入 `/api/admin/llm-runs` 的可观测项。

**③ 真实公告必须截断，而且要「智能截断」**

真实公告远长于合成样例（半年报可达数万字）。把整篇塞给本机 4B 模型会让单条推理失控
（实测一次 `--market-pages 30 --llm-limit 2` 在 1600 秒内跑不完）。
现按「与公告类型相关的关键词」挑段落（`llm_max_input_chars=6000`，最多 14 段），
实测把 23 段压到 3 段、27 段压到 9 段，单条耗时降到 60~70 秒。

#### 5.2.3 ★★ 15 条真实公告抽样：一致率 27% → 93%，问题在规格不在模型

用 `tools/sampling_review.py`（cninfo 全文检索「重大资产重组」，90 天窗口）抽了 **15 条真实公告**：

| 指标 | 值 |
|---|---|
| 样本数 | 15（dry-run，28.1 分钟，约 110 秒/条） |
| 通过证据闸门 | **15/15 = 100%** |
| `llm_schema_failure_rate` | **0.00** |
| 规则层与 LLM 判定一致率 | **4/15 = 27%** ← 看起来很差 |

**27% 是误导性的数字。** 看分歧模式：

| 模式 | 条数 | 真相 |
|---|:---:|---|
| `RESTRUCTURING → M&A` | 9 (60%) | **枚举边界没定义** |
| `RESTRUCTURING → 事件被丢弃` | 2 (13%) | **INV-EV1 太严** |
| 一致 | 4 | — |

##### 缺口 1：RESTRUCTURING 与 M&A 语义重叠，而 prompt 没说哪个优先

「重大资产重组」在 A 股是**监管分类**，其典型形态「发行股份购买资产」**本来就是并购**。
两个枚举大量重叠，模型选了更字面的 `M&A`。

**后果不只是「不一致」**：策略规则里 `C1` 只认 `RESTRUCTURING`
（`_DEAL_EVENTS = (RESTRUCTURING, BANKRUPTCY_REORGANIZATION)`），
所以模型说 `M&A` 时 **C1 不命中、coverage 直接少 0.35** —— **核心策略被静默削弱**。

**修法**（写进 prompt，而不是靠模型自觉）：

```
- RESTRUCTURING：受《上市公司重大资产重组管理办法》约束，或出现
  「重大资产重组」「发行股份购买资产」「资产置换」「借壳」「重组上市」等表述
- M&A：仅用于不构成重大资产重组的收购、合并、股权投资意向
- 两者都可能时，选 RESTRUCTURING
- 标题与正文冲突时以正文为准；正文确实不构成任何事件时选 OTHER
```

##### 缺口 2：INV-EV1 把「计划中的未来事件」当成违规

两条「重大资产重组部分限售股份上市流通」公告里，模型提取的 `event_time` 是
**未来日期**（公告说 9-14 上市流通），于是
`event_time(2026-09-14) > discovery_time(2026-09-11)` → INV-EV1 违反 → **整条事件被丢弃**。

但公告本来就经常预告未来事件（股东大会日、限售股上市日、资产交割日）。
规格 §40 的原意是「**区分**事件发生时间与系统发现时间，避免时间顺序错误」，
不是「禁止未来日期」。硬套会丢掉 13% 的公告。

**修法**：抽取结果增加 `event_time_kind`（`occurred` / `planned` / `inferred`）：

| kind | 含义 | INV-EV1 | 时效衰减 |
|---|---|---|---|
| `occurred` | 已发生 | **强制** `event_time ≤ discovery_time` | 按事件时间 |
| `planned` | 计划中（未来） | 不校验 | **按公告发布时间**（未来日期不能算「很新」） |
| `inferred` | 公告未写时间，回退为发布时间 | 不校验 | 按发布时间 |

##### 修复验证（同一批公告重跑 4 条）

| # | 修复前 | 修复后 | 结果 |
|:---:|---|---|---|
| 01 | `RESTRUCTURING` | `RESTRUCTURING` | 一致 |
| 02 | `M&A` | **`RESTRUCTURING`** | ✅ 边界规则生效 |
| 03 | **(丢弃)** | **`OTHER`** | ✅ 不再丢弃，且正文优先规则正确识别「限售股解禁 ≠ 重组」 |
| 04 | **(丢弃)** | `RESTRUCTURING` | ⚠ 不再丢弃，但**残留问题** |

**一致率 25% → 75%**，且两条原本被丢弃的事件都恢复了。

##### 修复后完整重跑（15 条）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 规则层与 LLM 一致率 | 4/15 = 27% | **14/15 = 93%** |
| 事件被丢弃 | 2 | **0** |
| 证据闸门通过 | 15/15 | 15/15 |
| `schema_failure_rate` | 0.00 | 0.00 |
| 耗时 | 28.1 分钟 | 19.9 分钟 |

9 条 `M&A` → `RESTRUCTURING` 全部纠正（边界规则生效）。

##### 唯一剩下的那条「分歧」：其实是**规则层错了，LLM 对了**

```
03  规则=RESTRUCTURING → LLM=OTHER
标题：中信证券……关于河北中瓷电子科技股份有限公司重大资产重组部分
      限售股份上市流通的核查意见
证据：「本次限售股上市类型为发行股份购买资产并募集配套资金……
      拟解除限售股共计 110,896,163 股」
```

这是**限售股解禁**，不是重组事件。规则层被标题里的「重大资产重组」骗了。

**这条假阳性比看上去严重**：每单重组完成后，**限售股解禁公告会连续产生数年**
（每批解禁一次），若被判成重组催化，会持续制造幻影机会。

因为它是**确定性可识别的标题模式**（不涉及「重组会不会成功」这类语义判断），
按规格 §28 应当由规则层处理，已加入负向关键词：

```python
RESTRUCTURING_NEGATIVE_KEYWORDS = ("限售股", "限售股份", "解除限售", "上市流通", "限售期")
```

实测效果：这类公告不再被预筛为 `RESTRUCTURING`（若同时涉及减持则归到减持，
否则整体跳过，根本不进入系统）。

##### 一个仍存在的模型行为（不是错误，但要知道）

同为限售股解禁内容，03（中信证券核查意见）判 `OTHER`、
04（公司自己的提示性公告）判 `RESTRUCTURING` —— 模型对同类内容的判定**不稳定**。
加入负向关键词后这两条都不再进入系统，因此**实际影响已消除**；
但同类不稳定可能出现在别的边界上，需要靠持续抽样跟踪。

> **下一步**：用修好的版本再抽 15 条，由人工在核对表上打勾确认准确率 ≥ 80%。
> 核对表：`data/cache/sampling/review_20260911_1959.md`

##### 结论：一致率不是正确的指标

真正该看的是**下游影响**：一条公告被判错，会不会让策略漏掉机会或误判机会？

| 缺口 | 下游影响 | 处理 |
|---|---|---|
| `M&A` 边界未定义 | **真问题**：C1 漏判 → 核心策略静默失效 | ✅ 已修（prompt） |
| `INV-EV1` 禁未来日期 | **真问题**：丢 13% 的公告 | ✅ 已修（`event_time_kind`） |
| 限售股假阳性 | **真问题**：持续制造幻影机会 | ✅ 已修（负向关键词） |
| 「一致率低」本身 | **不是问题**：规则层本就是廉价预筛 | 无需处理 |

#### 5.2.4 ★ 用户核对结果与「早期苗头」需求

##### 核对结果（人工打勾）

| 项 | 结果 |
|---|---|
| 准确率 | **14/15 = 93%**（门槛 ≥ 80%） |
| 判错的那条 | 第 03 条（限售股上市流通核查意见）—— 用户判**应为 `RESTRUCTURING`** |
| 结论 | 可以进入 `--live` |

##### 这条判断推翻了什么

第 03 条正是上一轮被我当作**假阳性排除**的「限售股解禁」类公告。
用户判定它**仍然是重组事件** —— 即 **召回优先于精确**。

但「召回优先」不等于「给它正常催化分」：这类公告在每单重组完成后
会**连续产生数年**，给正常催化分会持续制造幻影机会。

**因此改为按「阶段」区分，而不是排除：**

```
存量｜重组已完成（限售解禁 / 后续手续）  → 催化 5 分，early = False
```

事件照样入库、照样可检索（召回保留），但催化强度接近零，不会排到前面冒充新机会。

##### 用户提出的新需求：「有苗头的也要找」

> 对于**预重组 / 提请法院受理 / 法院受理 / 债权人提请重组**这种
> **还不太确定但有苗头**的也要查找，因为要**提前布局**。

在这之前，这些表述在关键词白名单里**完全不匹配** ——
它们根本不会进入系统，后面有再多分析能力也没用。

**已实现**（阶梯见 docs/06）：

| 公告标题 | 之前 | 现在 |
|---|---|---|
| 关于债权人申请对公司进行重整的公告 | ✗ 预筛丢弃 | 早期｜重整申请（15 分） |
| 关于法院裁定受理公司重整申请的公告 | ✗ 预筛丢弃 | 早期｜法院受理（28 分） |
| 关于预重整债权申报的公告 | ✗ 预筛丢弃 | 早期｜预重整（15 分） |
| 关于控股股东筹划重大事项停牌的公告 | ✗ 预筛丢弃 | 早期｜筹划停牌（10 分） |
| 关于签订股权投资意向协议的公告 | ✗ 预筛丢弃 | 早期｜意向协议（10 分） |

配套改动：

1. **`Opportunity.catalyst_stage` + `is_early_signal` 落库并出现在 API** ——
   用户要「提前布局」，但必须能一眼看出这是苗头而不是确定的事（§24 / §38）
2. **画像开关 `accept_early_signals`（默认开）** ——
   不是所有用户都想看低确定性信号；关掉后早期机会不产出卡片（§58 原则 6）
3. **C1 覆盖早期信号** —— 否则 coverage 上不去，机会卡根本不会生成
4. **无法识别阶段时保守按早期处理（20 分）** ——
   不能因为「不知道进展」就默认它已经推进得很深

##### 待办

- [ ] 前端 `OpportunityCard` 尚未渲染 `catalyst_stage`（API 已提供字段）
- [ ] 早期信号的**失效条件**要更敏感：法院不受理 / 申请被撤回 / 停牌后终止筹划
- [ ] 用含早期信号的样本再抽一次，确认识别正确率

##### 本环境的一个硬约束（影响候选池方案）

| 数据源 | 可达性 | 后果 |
|---|:---:|---|
| **cninfo（巨潮资讯）** | ✅ 可用（0.5 秒返回） | 公告与全市场按日查询均可用 |
| akshare → 东方财富 | ❌ **不可达**（连接失败） | **无法获取 ST 名单** |

因此候选池改为 **`--pool market`（事件优先）**：直接扫全市场公告 → 关键词预筛 → 候选公司。
这不只是绕开不可达依赖的权宜之计，反而更贴合规格 §5.9
（*「最终 Opportunity 不一定从股票池开始，也可以从『市场发生了什么？』开始」*）。

##### dry-run 的边界（必须知道）

dry-run **不写「判断类」数据**（Event / Evidence / Opportunity），因此：
- ✅ 能验证：采集质量、解析成功率、LLM 合规率、证据闸门、逐条抽取结果
- ❌ 看不到：最终**机会卡数量**（它需要 Event 落库）

要验证「机会卡」这一段，用 `--source mock`（确定性，覆盖 DB → 卡片）或 `--live`。

> **R3 的初步结论**：`qwen3:4b` 在这条样本上完全胜任抽取任务，**不必切换云端**。
> 但 1 条样本不足以定论 —— 仍需按 §5.1 在 dry-run 期间累计 ≥ 50 条再判定。
> 复现命令：`python tools/smoke_ollama.py`

### 5.3 第 3 天产出

一份 `data/cache/dry_run/report.md`：
- 三日漏斗与质量指标对照表
- 抽取质量人工抽样（随机 20 条，人工核对事件类型与证据段落是否准确）
- 是否开启真写的结论与理由

---

## 6. P9 · 启用真写与调度的前置条件

同时满足才允许 `INGEST_DRY_RUN=false` + `SCHEDULER_ENABLED=true`：

- [ ] 三个交易日 dry-run 指标全部达标（§5.1）
- [ ] 人工抽样 20 条的抽取准确率 ≥ 80%
- [ ] `evidence_rejected` 中无「改写原文」类问题
- [ ] 幂等测试通过（同一批数据跑两次行数不变）
- [ ] 有失败项可追溯（`/api/admin/runs/{id}/errors` 可用）
- [ ] 交易日历可取（否则显式接受「工作日=交易日」的退化并知晓节假日会空跑）

---

## 7. 风险登记册与检查点

| # | 风险 | 触发信号 | 检查点 | 缓解 |
|:---:|---|---|---|---|
| R1 | 采集范围聚焦漏机会 | 用户觉得「没有我关心的机会」 | P6 验收 | 已改为 **事件优先**（`--pool market`）扫全市场，不再受 ST 名单限制 |
| R2 | cninfo 反爬 / PDF 质量 | `parse_failure_rate > 0.15` | 每日 dry-run（**真实数据实测 0.00，见 §5.2.2**） | 解析失败率可观测；扫描件降级 `needs_ocr`；失败项逐条可查 |
| R3 | `qwen3:4b` 抽取质量 | `llm_schema_failure_rate`、`evidence_rejected` | 干预期第 3 天（**合成样例 §5.2.1 + 真实公告 §5.2.2 均通过**） | provider 两行切换；智能截断；证据闸门拦截幻觉 |
| R4 | 双分数让用户困惑 | 用户问「82 和 76 哪个算数」 | P8 人工验收 | 规则分为主、语义分只在详情页、分歧 >20 才提示 |
| R5 | 交易日历依赖单一源 | `calendar_ok=false` | 启动自检 | 退化为工作日 + 显式日志告警 |
| R6 | 规格 §12/§13/§14 数值不自洽 | 算例无法复现 82（得 68） | OQ-01 已确认 | 以 §12 权重为准，§13/§14/§17 作展示参考 |
| R7 | SQLite 并发写 | 采集与 API 同时写冲突 | P9 | 单进程调度；必要时开 WAL；上量表迁 Postgres |
| R8 | 本机 4B 推理慢 | 单轮 > 6 小时 | 每日 dry-run | 降采样；抽取层切云端 |

---

## 8. 待你决策 / 待验证事项

| ID | 事项 | 现状 | 影响 |
|:---:|---|---|---|
| **OQ-01** ✅ 已确认 | **规格 §12 权重、§13 拆解示例（79）、§14 维度分（加权 72.75）、§17 卡片（82）四处数值不自洽** | 本项目采用「以 §12 权重为准、自洽公式、可复算」；`04` §8 算例得 **68** 而非 82 | 若你要求严格复现 §13 的 `+25/+20/...` 数值，则必须放弃 §12 权重表。**两者不可兼得** |
| OQ-02 | 第一个实现的策略是否确认为 `restructuring` | 按你「可以先做重组的」定 | 若是，P6 直接开工 |
| OQ-03 | 抽取层模型最终用本地还是云端 | 待 P7 第 3 天数据判定 | 影响 API 成本与数据外流 |
| OQ-04 | 交易日历退化是否可接受 | 取不到时退化为「工作日=交易日」 | 节假日会空跑一次（无害但浪费） |
| OQ-05 | `HISTORY_CASE`（历史相似案例）何时启用 | MVP 权重 0 | 启用需先建历史案例库（P2 级功能） |
| OQ-06 | 语义分是否在卡片上显示 | 当前设计：只在详情页 | 影响用户对「两个数字」的困惑度 |
| OQ-07 | 是否需要 `needs_ocr` 的 OCR 落地 | 本轮只标记不处理 | 扫描件公告将永久只能点到外部链接 |

---

## 9. 后续路线（P10 之后）

| 优先级 | 功能 | 出处 |
|:---:|---|---|
| P1 | Thesis 自动监控与失效提醒（完整） | §22 / M7-04 |
| P1 | 新闻聚类展示（事件簇） | §43 / M2-10 |
| P1 | 自然语言策略 → 结构化 → 确认 | §6 / M1-04 |
| P1 | 反馈闭环驱动权重（含用户锁定） | §36 / M1-06 |
| P2 | 历史重组案例数据库 | §57 |
| P2 | 语义检索（自然语言查询 → 条件组合） | §49 / M8-05 |
| P2 | 公司关系图谱（轻量知识图谱） | §48 / M8-04 |
| P2 | 事件概率统计 / 策略回测 | §57 |
| P2 | 投资研究报告生成 | §57 |
| P2 | 向量化证据检索（`04` 预留，表结构已定） | §49 |
| P3 | Portfolio Thesis 管理 | §57 |
| P3 | 多空辩论 Agent（LangGraph 迁移，D06 复议） | §50 |

---

## 10. 每个阶段结束必须回答的问题

> **P3~P9 每阶段收尾时，回答这三个问题，而不是「写了多少代码」：**

1. 这一阶段让 **S-01（30 秒找到 1~3 个值得研究的机会）** 变短了吗？
2. 这一阶段让 **S-02（2 分钟理解为什么值得关注 + 什么没确认）** 变短了吗？
3. 这一阶段有没有引入**无法解释、无法追溯、无法复现**的东西？

**若第 3 问的答案是「有」，立即回退该部分，而不是等下一阶段优化。**
