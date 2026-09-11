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
| `test_scoring.py` | `04` §8 的算例必须复算出 `rule_score = 69` | M6-03 |
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

`INGEST_DRY_RUN=true` 期间，采集与 LLM 全部真实执行，**只是不写业务表**。

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
| R1 | 采集范围聚焦漏机会 | 用户觉得「没有我关心的机会」 | P6 验收 | `scope` 一行配置放宽；文档已声明取舍 |
| R2 | cninfo 反爬 / PDF 质量 | `parse_failure_rate > 0.15` | 每日 dry-run | 解析失败率可观测；扫描件降级 `needs_ocr`；失败项逐条可查 |
| R3 | `qwen3:4b` 抽取质量 | `llm_schema_failure_rate`、`evidence_rejected` | 干预期第 3 天 | provider 两行切换；证据闸门拦截幻觉 |
| R4 | 双分数让用户困惑 | 用户问「82 和 76 哪个算数」 | P8 人工验收 | 规则分为主、语义分只在详情页、分歧 >20 才提示 |
| R5 | 交易日历依赖单一源 | `calendar_ok=false` | 启动自检 | 退化为工作日 + 显式日志告警 |
| R6 | 规格 §12/§13/§14 数值不自洽 | 算例无法复现 82（得 69） | **OQ-01 待你确认** | 以 §12 权重为准，§13/§14 作展示参考 |
| R7 | SQLite 并发写 | 采集与 API 同时写冲突 | P9 | 单进程调度；必要时开 WAL；上量表迁 Postgres |
| R8 | 本机 4B 推理慢 | 单轮 > 6 小时 | 每日 dry-run | 降采样；抽取层切云端 |

---

## 8. 待你决策 / 待验证事项

| ID | 事项 | 现状 | 影响 |
|:---:|---|---|---|
| **OQ-01** | **规格 §12 权重、§13 拆解示例（79）、§14 维度分（加权 72.75）、§17 卡片（82）四处数值不自洽** | 本项目采用「以 §12 权重为准、自洽公式、可复算」；`04` §8 算例得 **69** 而非 82 | 若你要求严格复现 §13 的 `+25/+20/...` 数值，则必须放弃 §12 权重表。**两者不可兼得** |
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
