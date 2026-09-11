# 05 · API 契约

> `backend/app/api/` 实现的 REST 接口。前端 `frontend/src/api/` 按此生成 TypeScript 类型。
> **契约先行**：接口变更必须先改本文件，再改代码。

---

## 0. 通用约定

### 0.1 基础

```
Base URL   http://localhost:8000/api
认证        无（本地单用户，D02；端口仅监听 127.0.0.1）
时间        全部 ISO 8601 UTC（如 2026-09-08T11:32:00Z），前端转 Asia/Shanghai 展示
金额/比例   数值型，比例用 0~1 小数（如 0.94），不用百分号字符串
```

### 0.2 响应信封

```jsonc
// 成功（单对象）
{ "data": { ... }, "meta": { "generated_at": "2026-09-11T07:40:00Z" } }

// 成功（列表）
{
  "data": [ ... ],
  "page": { "limit": 20, "offset": 0, "total": 137, "has_more": true },
  "meta": { "generated_at": "..." }
}

// 错误
{
  "error": {
    "code": "OPPORTUNITY_NOT_FOUND",
    "message": "机会不存在",
    "detail": { "opportunity_id": 999 }
  }
}
```

### 0.3 错误码

| HTTP | code | 场景 |
|:---:|---|---|
| 400 | `INVALID_PARAM` | 参数校验失败 |
| 404 | `*_NOT_FOUND` | 资源不存在 |
| 409 | `INVALID_STATUS_TRANSITION` | 状态迁移非法（违反状态机） |
| 422 | `RULE_VIOLATION` | 违反不变量（如给 Thesis 存空的失效条件） |
| 503 | `LLM_UNAVAILABLE` | Ollama/云端不可用 |
| 503 | `INGEST_SOURCE_UNAVAILABLE` | cninfo/akshare 不可用 |

### 0.4 枚举值（与 `02-领域模型与数据模型` §3 一致）

```
status          : discovered | pending_confirmation | tracking | thesis_confirmed
                  | observing | invalidated | archived
thesis_type     : restructuring | turnaround | value | growth | event_driven
                  | policy | cycle | product | shareholder_action | ma_integration
event_type      : M&A | RESTRUCTURING | ASSET_INJECTION | CONTROL_CHANGE
                  | SHAREHOLDER_BUY | SHAREHOLDER_SELL | BUYBACK
                  | BANKRUPTCY_REORGANIZATION | EARNINGS_TURNAROUND | POLICY_CATALYST
                  | MAJOR_CONTRACT | NEW_PRODUCT | MANAGEMENT_CHANGE
                  | REGULATORY_RISK | LITIGATION | DIVIDEND_POLICY | OTHER
reliability     : A | B | C | D | E
assertion_kind  : fact | inference | hypothesis | market_discussion
freshness       : new | updated | breaking | stale
```

---

## 1. 健康检查

### `GET /api/health`

```jsonc
{
  "data": {
    "status": "ok",
    "db": "ok",
    "llm": { "provider": "ollama", "model": "qwen3:4b", "reachable": true },
    "scheduler": { "enabled": false, "dry_run": true,
                   "calendar_ok": true, "next_run": "2026-09-12T00:30:00Z" }
  }
}
```

---

## 2. Radar 首页（§18 / M10-01）

### `GET /api/radar`

一次请求返回首页所需的全部数据（避免前端瀑布式请求）。

```jsonc
{
  "data": {
    "profile": {
      "id": 1,
      "name": "我的画像",
      "top_weights": [
        { "thesis_type": "restructuring", "weight": 0.40, "display_name": "重组预期" },
        { "thesis_type": "turnaround",    "weight": 0.25, "display_name": "困境反转" },
        { "thesis_type": "asset_injection","weight": 0.15, "display_name": "资产注入" }
      ],
      "auto_learn_enabled": true,
      "locked_weights": ["restructuring"]
    },

    "today": {
      "date": "2026-09-11",
      "is_trading_day": true,
      "new_count": 12,
      "cards": [ /* OpportunityCard[]，见 §3.2，按 rule_score 降序，默认 6 张 */ ]
    },

    "counts": {
      "discovered": 5,
      "pending_confirmation": 12,
      "tracking": 8,
      "thesis_confirmed": 3,
      "invalidated": 2
    },

    "recent_events": [
      {
        "id": 501,
        "company": { "id": 88, "name": "ST XXX", "code": "600xxx" },
        "event_type": "RESTRUCTURING",
        "title": "重大资产重组进展",
        "summary": "……",
        "importance": 0.91,
        "certainty_level": "disclosed",
        "event_time": "2026-09-08T11:32:00Z",
        "discovery_time": "2026-09-08T11:35:12Z",
        "freshness": "stale",
        "relative_time": "3天前",
        "evidence_ids": [1001, 1002]
      }
    ],

    "alerts": [
      {
        "id": 7,
        "alert_type": "thesis_invalidated",
        "title": "投资逻辑发生重大变化",
        "message": "你关注的重组预期逻辑出现失效事件",
        "score_before": 94, "score_after": 31,
        "is_read": false,
        "created_at": "2026-09-11T07:20:00Z"
      }
    ],

    "pipeline": {
      "last_run_at": "2026-09-11T00:32:00Z",
      "dry_run": true,
      "funnel": { "candidates": 301, "announcements_fetched": 74,
                  "passed_prefilter": 51, "events_extracted": 43,
                  "thesis_candidates": 19, "deep_analyzed": 8, "cards": 6 },
      "quality": { "parse_failure_rate": 0.04,
                   "llm_schema_failure_rate": 0.06,
                   "evidence_rejected": 3 }
    }
  }
}
```

---

## 3. 机会

### 3.1 `GET /api/opportunities`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `status` | csv | — | 多选，如 `pending_confirmation,tracking` |
| `thesis_type` | csv | — | 多选 |
| `company_id` | int | — | |
| `min_rule_score` | float | — | |
| `max_risk_score` | float | — | |
| `freshness` | csv | — | `new,updated,breaking` |
| `sort` | enum | `rule_score` | `rule_score` \| `match_score` \| `event_strength` \| `certainty` \| `last_updated_at` |
| `order` | enum | `desc` | |
| `limit` / `offset` | int | 20 / 0 | |

```jsonc
{
  "data": [ /* OpportunityCard[] */ ],
  "page": { "limit": 20, "offset": 0, "total": 37, "has_more": true }
}
```

### 3.2 `OpportunityCard`（§17 的接口形态）

```jsonc
{
  "id": 301,
  "company": { "id": 88, "name": "ST XXX", "code": "600xxx",
               "industry": "……", "is_st": true },
  "thesis_type": "restructuring",
  "thesis_display_name": "重组预期",
  "status": "pending_confirmation",
  "status_label": "待确认",

  "match_score": 94,
  "rule_score": 69,
  "risk_score": 43,
  "semantic_score": 76,
  "divergence": 7,
  "divergence_flagged": false,

  "why_in_radar": [                      // §17「为什么进入你的关注池？」
    "发布重大资产重组公告",
    "控股股东近期发生变化",
    "公司连续亏损",
    "存在潜在资产注入"
  ],

  "latest_events": [
    { "event_type": "RESTRUCTURING", "title": "重大资产重组公告",
      "event_time": "2026-09-08T11:32:00Z", "relative_time": "3天前", "freshness": "stale" },
    { "event_type": "CONTROL_CHANGE", "title": "控股股东变更",
      "event_time": "2026-09-06T09:10:00Z", "relative_time": "5天前", "freshness": "stale" },
    { "event_type": "REGULATORY_RISK", "title": "股票异常波动公告",
      "event_time": "2026-09-02T12:00:00Z", "relative_time": "9天前", "freshness": "stale" }
  ],

  "ai_judgement": "该标的高度符合你的重组预期策略。但目前仍处于方案确认阶段，核心资产及交易对价尚未完全明确。",
  "assertion_kind": "inference",

  "evidence_count": 3,
  "open_question_count": 4,
  "risk_count": 2,
  "a_grade_evidence_count": 2,
  "only_market_discussion": false,       // true 时前端强制显示警示横幅（M4-04）

  "first_discovered_at": "2026-09-08T19:40:00Z",
  "last_updated_at": "2026-09-11T07:20:00Z"
}
```

### 3.3 `GET /api/opportunities/{id}`（§34.3 Opportunity Detail）

```jsonc
{
  "data": {
    "card": { /* OpportunityCard */ },

    "thesis": {
      "id": 12,
      "thesis_type": "restructuring",
      "statement": "公司处于 ST 状态，同时出现重大资产重组及控制权变化，因此存在潜在重组预期。",
      "why_now": {
        "past": "公司长期处于亏损状态",
        "recent": "控股股东发生变化",
        "this_week": "公司发布重大资产重组公告",
        "conclusion": "因此该公司首次进入「重组预期」机会池"
      },
      "supporting_evidence": [ /* EvidenceRef[] */ ],
      "contradictory_evidence": [
        { "kind": "history_failure",
          "description": "公司曾于 2024 年筹划重组但终止",
          "evidence_ids": [1005] },
        { "kind": "regulatory",
          "description": "交易所已就本次重组发出问询函",
          "evidence_ids": [1006] }
      ],
      "invalidating_events": [
        { "event_type": "RESTRUCTURING", "severity": "terminal",
          "description": "重组终止 / 重大资产重组失败" },
        { "event_type": "CONTROL_CHANGE", "severity": "severe",
          "description": "控股权变更取消" }
      ]
    },

    "open_questions": [
      { "id": 1, "question": "交易标的", "status": "open" },
      { "id": 2, "question": "交易价格", "status": "open" },
      { "id": 3, "question": "重组方案", "status": "open" },
      { "id": 4, "question": "监管审核结果", "status": "open" },
      { "id": 5, "question": "重大资产重组公告", "status": "confirmed",
        "confirmed_evidence_id": 1001 },
      { "id": 6, "question": "控股股东发生变化", "status": "confirmed",
        "confirmed_evidence_id": 1002 },
      { "id": 7, "question": "ST 状态", "status": "confirmed" }
    ],

    "risks": [
      { "factor": "事件失败可能性", "weight": 0.25, "severity": "high",
        "description": "重组存在失败可能，公司历史上有终止记录" },
      { "factor": "公司基本面恶化", "weight": 0.20, "severity": "medium_high",
        "description": "连续亏损，净资产承压" }
    ],

    "next_events_to_watch": [
      "重组方案公告", "交易所问询回复", "资产评估结果", "股东大会", "监管审核"
    ],

    "timeline": [
      { "date": "2026-09-02", "event_type": "REGULATORY_RISK",
        "title": "股票异常波动公告", "evidence_id": 1003 },
      { "date": "2026-09-06", "event_type": "CONTROL_CHANGE",
        "title": "控股股东变更", "evidence_id": 1002 },
      { "date": "2026-09-08", "event_type": "RESTRUCTURING",
        "title": "重大资产重组公告", "evidence_id": 1001 }
    ],

    "financials": [
      { "period": "2026H1", "metrics": {
          "revenue": { "value": 20.1, "unit": "亿元", "yoy": 0.23 },
          "net_profit": { "value": -1.2, "unit": "亿元", "yoy": null },
          "ocf": { "value": 0.8, "unit": "亿元", "yoy": 0.41 },
          "gross_margin": { "value": 0.18, "yoy": -0.042 },
          "debt_ratio": { "value": 0.68, "yoy": 0.08 }
        } }
    ],

    "news_clusters": [
      { "id": 5, "label": "围绕 ST XXX 重组事件的 17 条报道",
        "member_count": 17,
        "key_points": ["重大资产重组", "控股权变化", "市场对重组预期的讨论"],
        "last_seen": "2026-09-10T14:00:00Z" }
    ],

    "market": {                            // ★ 辅助信息层（§46），不是首页主体
      "price": 4.12, "change_pct": 0.032, "volume": 12345678,
      "market_cap": 32.1, "pe": null, "pb": 1.8,
      "note": "行情数据仅作辅助信息，用于观察事件后的价格行为"
    },

    "status_history": [
      { "from_status": null, "to_status": "discovered",
        "reason": "进入重组预期候选池", "changed_at": "2026-09-08T19:40:00Z" },
      { "from_status": "discovered", "to_status": "pending_confirmation",
        "reason": "存在 4 项待确认事项", "changed_at": "2026-09-08T20:05:00Z" }
    ]
  }
}
```

### 3.4 `GET /api/opportunities/{id}/score-breakdown`

**可解释性的核心接口**（§13 / M6-03）。

```jsonc
{
  "data": {
    "rule_score": 69,
    "semantic_score": 76,
    "divergence": 7,
    "divergence_flagged": false,
    "risk_score": 43,
    "risk_penalty": -6.45,
    "score_version": "rules-1.0+weights-restructuring-1.0",
    "computed_at": "2026-09-11T07:20:00Z",
    "disclaimer": "评分为概率性研究线索的相对排序信号，不构成投资建议，不代表收益预期。",

    "dimensions": [
      {
        "dimension": "THESIS_MATCH",
        "display_name": "逻辑匹配",
        "raw_value": 94,
        "weight": 0.30,
        "weighted_value": 28.20,
        "direction": "positive",
        "items": [
          { "rule_id": "R-RS-TM-01", "delta": 94,
            "reason": "命中策略核心条件 3.76/4（重组公告、控制权变更、资产注入迹象）",
            "evidence_ids": [1001, 1002, 1004] }
        ]
      },
      {
        "dimension": "EVENT_CATALYST",
        "display_name": "事件催化",
        "raw_value": 91,
        "weight": 0.25,
        "weighted_value": 22.75,
        "direction": "positive",
        "items": [
          { "rule_id": "R-GEN-EV-01", "delta": 40,
            "reason": "存在 A 类公告直接对应重大资产重组", "evidence_ids": [1001] },
          { "rule_id": "R-GEN-EV-02", "delta": 25,
            "reason": "同期出现控制权变更（组合催化）", "evidence_ids": [1002] },
          { "rule_id": "R-GEN-EV-03", "delta": 15,
            "reason": "涉及金额达显著阈值", "evidence_ids": [1004] },
          { "rule_id": "R-GEN-EV-04", "delta": 10,
            "reason": "牵涉知名产业方", "evidence_ids": [1001] },
          { "rule_id": "R-GEN-EV-TIME", "delta": 1,
            "reason": "时效衰减系数 1.00（≤1天）", "evidence_ids": [] }
        ]
      },
      {
        "dimension": "RISK",
        "display_name": "风险",
        "raw_value": 43,
        "weight": "penalty",
        "weighted_value": -6.45,
        "direction": "negative",
        "direction_note": "★ 数值越高代表风险越大（与上方维度方向相反）",
        "items": [
          { "rule_id": "R-GEN-RK-01", "delta": 25,
            "reason": "事件失败可能性：高（历史有终止记录、方案未定）",
            "evidence_ids": [1005] },
          { "rule_id": "R-GEN-RK-02", "delta": 20,
            "reason": "公司基本面恶化：中高", "evidence_ids": [] }
        ]
      }
    ],

    "semantic_factors": [
      { "direction": "positive",
        "factor": "受让方为有成功重整记录的产业集团",
        "rationale": "……", "evidence_ids": [1001] },
      { "direction": "negative",
        "factor": "本次交易结构与 2024 年失败案例存在结构相似性",
        "rationale": "……", "evidence_ids": [1005] }
    ],
    "rules_already_covered": ["事件类型命中", "公告数量", "ST 状态", "财务状况", "市场关注度"]
  }
}
```

### 3.5 `GET /api/opportunities/{id}/evidence`

```jsonc
{
  "data": [
    {
      "id": 1001,
      "source_type": "announcement",
      "source_name": "巨潮资讯",
      "title": "关于重大资产重组进展的公告",
      "source_url": "http://static.cninfo.com.cn/...pdf",
      "publication_time": "2026-09-08T11:32:00Z",
      "reliability_level": "A",
      "reliability_note": "公司正式公告 / 交易所披露",
      "assertion_kind": "fact",
      "document_id": "ANN-2026-0908-001",
      "relevant_text": "……公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份……",
      "page": 2, "para_index": 7,
      "extracted_facts": ["控股股东拟转让全部股份", "受让方为 XX 集团"],
      "confidence": 0.96,
      "supports_thesis": ["restructuring", "shareholder_action"]
    }
  ]
}
```

### 3.6 `POST /api/opportunities/{id}/actions`（§35 / M9-01）

```jsonc
// 请求
{
  "action": "confirmed",              // viewed | confirmed | ignored | tracked
  "reason_thesis_types": ["restructuring", "control_change"]   // confirmed 时可选
}

// 响应
{ "data": { "action_id": 55, "new_status": "tracking",
            "message": "已加入跟踪：重组预期" } }
```

**约束**：单张卡片上的可执行动作 ≤4 个（M9-01）。

### 3.7 `PATCH /api/opportunities/{id}/status`

```jsonc
{ "to_status": "invalidated", "reason": "重组终止公告" }
```

非法迁移返回 `409 INVALID_STATUS_TRANSITION`，并在 `detail.allowed` 中给出允许的目标状态。

---

## 4. 事件

### `GET /api/events`

| 参数 | 说明 |
|---|---|
| `event_type` | csv 多选 |
| `company_id` | |
| `since` / `until` | ISO 时间 |
| `min_importance` | 0~1 |
| `is_invalidating` | bool，只看失效事件 |
| `sort` | `event_time` \| `importance` \| `discovery_time` |

```jsonc
{
  "data": [
    { "id": 501, "company": { "id": 88, "name": "ST XXX", "code": "600xxx" },
      "event_type": "RESTRUCTURING", "event_type_label": "重大资产重组",
      "title": "重大资产重组进展", "summary": "……",
      "event_time": "2026-09-08T11:32:00Z",
      "discovery_time": "2026-09-08T11:35:12Z",
      "time_note": "事件发生时间 3 天前 · 系统发现 3 天前",
      "importance": 0.91, "certainty": 0.88, "certainty_level": "disclosed",
      "source_type": "announcement", "source_url": "http://...",
      "affected_thesis": ["restructuring", "turnaround"],
      "evidence_ids": [1001],
      "is_invalidating": false,
      "freshness": "stale", "relative_time": "3天前" }
  ],
  "page": { "limit": 50, "offset": 0, "total": 214, "has_more": true }
}
```

### `GET /api/events/clusters`

新闻聚类（§42 / M2-10）——**不返回 17 张重复卡片**。

```jsonc
{
  "data": [
    { "id": 5, "label": "围绕 ST XXX 重组事件的 17 条报道",
      "company": { "id": 88, "name": "ST XXX" },
      "event_type": "RESTRUCTURING",
      "member_count": 17,
      "key_points": ["重大资产重组", "控股权变化", "市场对重组预期的讨论"],
      "sources": [ { "name": "证券时报", "reliability": "C", "count": 6 },
                   { "name": "东方财富", "reliability": "C", "count": 5 },
                   { "name": "股吧讨论", "reliability": "E", "count": 6 } ],
      "first_seen": "2026-09-08T12:00:00Z", "last_seen": "2026-09-10T14:00:00Z",
      "members": [ /* 可选，默认不返回 */ ] }
  ]
}
```

---

## 5. Thesis / My Thesis（§34.4 / M10-05）

### `GET /api/thesis`

```jsonc
{
  "data": {
    "total_tracking": 12,
    "by_type": [
      { "thesis_type": "restructuring", "display_name": "重组预期", "count": 5 },
      { "thesis_type": "turnaround",    "display_name": "困境反转", "count": 4 },
      { "thesis_type": "event_driven",  "display_name": "事件驱动", "count": 3 }
    ],
    "items": [
      { "id": 12, "company": { "id": 88, "name": "ST XXX", "code": "600xxx" },
        "thesis_type": "restructuring",
        "statement": "公司处于 ST 状态，同时出现重大资产重组及控制权变化，因此存在潜在重组预期。",
        "status": "pending_confirmation",
        "rule_score": 69, "risk_score": 43,
        "evidence_count": 3, "contradictory_count": 2, "open_question_count": 4,
        "invalidating_events": ["重组终止", "重大资产重组失败", "控股权变化取消"],
        "last_updated_at": "2026-09-11T07:20:00Z",
        "has_unread_alert": true }
    ]
  }
}
```

### `GET /api/thesis/{id}`

返回单条 Thesis 详情，结构同 `3.3` 的 `thesis` 字段 + 关联机会链接。

---

## 6. 画像与策略

### `GET /api/profile` / `PUT /api/profile`

```jsonc
// PUT 请求体（部分更新）
{
  "name": "我的画像",
  "horizon": "mid",
  "markets": ["A股"],
  "industry_prefs": [],
  "exclusions": ["银行", "白酒"],
  "auto_learn_enabled": true,
  "locked_weights": ["restructuring"]
}
```

### `GET /api/profile/weights` / `PUT /api/profile/weights`

```jsonc
// GET 响应
{
  "data": {
    "weights": [
      { "thesis_type": "restructuring",     "display_name": "重组预期",   "weight": 0.40, "source": "manual",  "locked": true },
      { "thesis_type": "turnaround",        "display_name": "困境反转",   "weight": 0.25, "source": "template:重组猎手" },
      { "thesis_type": "asset_injection",   "display_name": "资产注入",   "weight": 0.15, "source": "manual" },
      { "thesis_type": "shareholder_action","display_name": "大股东增持","weight": 0.10, "source": "manual" },
      { "thesis_type": "policy",            "display_name": "政策驱动",   "weight": 0.10, "source": "manual" }
    ],
    "normalized_note": "权重之和不必为 100%，系统读取时归一化",
    "ai_suggested": [                     // 反馈闭环建议（M1-06）
      { "thesis_type": "restructuring", "current": 0.40, "suggested": 0.48,
        "basis": "近 30 天你确认关注的机会中 12/14 为重组类" },
      { "thesis_type": "value", "current": 0.10, "suggested": 0.03,
        "basis": "近 30 天你忽略了 9/9 个价值类机会" }
    ]
  }
}
```

**约束**：`locked: true` 的项，`ai_suggested` 不得自动生效（INV-P1）。

### `GET /api/strategies`

策略注册表（D13：设计全量，实现逐个）。

```jsonc
{
  "data": [
    { "code": "restructuring", "display_name": "重组预期",
      "status": "implemented", "description": "……",
      "support_event_types": ["RESTRUCTURING", "ASSET_INJECTION", "CONTROL_CHANGE",
                              "BANKRUPTCY_REORGANIZATION", "M&A"],
      "invalidating_event_types": ["RESTRUCTURING", "CONTROL_CHANGE"],
      "core_condition_count": 4,
      "default_weights": { "THESIS_MATCH": 0.30, "EVENT_CATALYST": 0.25,
                           "CATALYST_STRENGTH": 0.10, "CERTAINTY": 0.10,
                           "FUNDAMENTALS": 0.05, "SHAREHOLDER_STRUCTURE": 0.10,
                           "MARKET_ATTENTION": 0.05, "HISTORY_CASE": 0.00 } },
    { "code": "turnaround", "display_name": "困境反转",
      "status": "designed", "description": "……（schema 已定，实现待排期）" }
  ]
}
```

### `POST /api/strategies/parse-nl`（P1，§6 / M1-04）

```jsonc
// 请求
{ "text": "我想找那些连续亏损，但是最近出现重组、资产注入或者大股东变化迹象的 ST 股票。" }

// 响应：★ 必须先复述并等用户确认，再执行扫描
{
  "data": {
    "interpretation": "我理解你的投资策略为：寻找处于经营困境、具有 ST 属性，同时近期出现重组、资产注入或控制权变化迹象的公司。",
    "structured": {
      "market": ["A股"],
      "stock_tags": ["ST", "*ST"],
      "financial_conditions": ["连续亏损"],
      "events": ["RESTRUCTURING", "ASSET_INJECTION", "CONTROL_CHANGE"],
      "weights": { "restructuring": 0.35, "asset_injection": 0.25,
                   "shareholder_change": 0.20, "st_status": 0.10,
                   "market_attention": 0.10 }
    },
    "unresolved": ["未指定市场范围（默认 A 股）"],
    "requires_confirmation": true
  }
}
```

---

## 7. 提醒

```
GET   /api/alerts?is_read=false
POST  /api/alerts/{id}/read
POST  /api/alerts/read-all
```

```jsonc
{
  "data": [
    { "id": 7, "alert_type": "thesis_invalidated",
      "title": "投资逻辑发生重大变化",
      "message": "ST XXX：你关注的重组预期逻辑出现失效事件——重大资产重组终止",
      "opportunity_id": 301,
      "score_before": 94, "score_after": 31,
      "triggered_by_event_id": 512,
      "suggestion": "建议重新评估该机会",
      "is_read": false, "created_at": "2026-09-11T07:20:00Z" }
  ]
}
```

---

## 8. 管理与可观测（M2-13）

### `POST /api/admin/ingest`

手动触发采集（`SCHEDULER_ENABLED=false` 时的唯一入口）。

```jsonc
// 请求
{ "stage": "incremental",     // full | incremental | rescore
  "run_id": null,              // 指定则继续该次运行
  "limit": 5 }                 // 每级最大处理条数（dry-run 调试用）

// 响应（异步任务受理）
{ "data": { "task_id": "ing-20260911-001", "status": "accepted",
            "dry_run": true, "stream_url": "/api/admin/runs/42/stream" } }
```

### `GET /api/admin/runs`

```jsonc
{
  "data": [
    { "id": 42, "adapter": "cninfo", "scope": "st_and_risk_warning",
      "dry_run": true,
      "started_at": "2026-09-11T00:30:00Z", "finished_at": "2026-09-11T00:37:52Z",
      "items_found": 74, "items_new": 31, "items_skipped": 43, "items_failed": 2,
      "parse_failed": 3, "parse_needs_ocr": 1,
      "parse_failure_rate": 0.041, "field_missing_rate": 0.11,
      "funnel": { "candidates": 301, "announcements_fetched": 74,
                  "passed_prefilter": 51, "events_extracted": 43,
                  "thesis_candidates": 19, "deep_analyzed": 8, "cards": 6 },
      "quality": { "llm_schema_failure_rate": 0.06, "evidence_rejected": 3 },
      "error_summary": "2 条公告详情请求超时" }
  ]
}
```

### `GET /api/admin/runs/{id}/errors`

逐条列出失败项，**不隐藏失败**（R2 缓解）。

```jsonc
{ "data": [ { "stage": "pdf_parse", "document_id": "ANN-2026-0908-077",
              "source_url": "http://...", "error": "scanned_pdf_no_text_layer",
              "action": "stored_link_only, marked needs_ocr" } ] }
```

### `GET /api/admin/llm-runs?node=extract_event&status=schema_error`

```jsonc
{ "data": [ { "id": 881, "node_name": "extract_event", "model": "qwen3:4b",
              "prompt_version": "v3", "status": "schema_error",
              "attempt": 3, "latency_ms": 18320,
              "error": "field 'evidence_slices' missing" } ],
  "meta": { "schema_failure_rate": 0.06,
            "note": "该指标是判断本机 4B 模型能否胜任抽取任务的直接依据（R3）" } }
```

---

## 9. 前端 TypeScript 类型映射

`frontend/src/api/types.ts` 由本契约直接翻译，关键类型：

```typescript
export type OpportunityStatus =
  | 'discovered' | 'pending_confirmation' | 'tracking'
  | 'thesis_confirmed' | 'observing' | 'invalidated' | 'archived';

export type ThesisType =
  | 'restructuring' | 'turnaround' | 'value' | 'growth' | 'event_driven'
  | 'policy' | 'cycle' | 'product' | 'shareholder_action' | 'ma_integration';

export interface ScoreDimension {
  dimension: string;
  display_name: string;
  raw_value: number;
  weight: number | 'penalty';
  weighted_value: number;
  direction: 'positive' | 'negative';
  direction_note?: string;            // 风险维度必须带方向说明
  items: ScoreItem[];
}

export interface ScoreItem {
  rule_id: string;                    // 可追溯到规则源码
  delta: number;
  reason: string;
  evidence_ids: number[];             // 可追溯到原文段落
}
```

---

## 10. 契约测试要求

| 测试 | 内容 |
|---|---|
| Schema 快照测试 | `GET /api/radar`、`/api/opportunities/{id}` 响应结构快照，防止意外破坏前端 |
| 不变量测试 | `PATCH status` 非法迁移必须返回 409 |
| 可解释性测试 | `/score-breakdown` 返回的每个 `ScoreItem.evidence_ids` 必须在 Evidence 表中存在 |
| 免责声明测试 | 每个含分数的响应必须带 `disclaimer` 字段 |
| 方向标注测试 | 风险维度必须带 `direction_note`（防止用户误读方向） |
