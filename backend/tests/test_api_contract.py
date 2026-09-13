"""API 契约测试（docs/05 §10）。

**契约先行**：接口结构变化必须同时改 docs/05 与本文件。
本文件重点守三件事：

1. 任何含分数的响应都必须带 ``disclaimer``（规格第 60 节第 21 条）
2. 风险维度必须带方向标注（避免「风险 43」被误读为低风险或高风险）
3. 首页与详情页不得以行情 / K 线为主体（规格 §19 / M10-08）
"""

from __future__ import annotations

import pytest

from app.models.enums import ThesisType

REQUIRED_CARD_FIELDS = {
    "id", "company", "thesis_type", "thesis_display_name", "status", "status_label",
    "match_score", "rule_score", "risk_score", "semantic_score", "divergence",
    "divergence_flagged", "why_in_radar", "latest_events", "ai_judgement",
    "evidence_count", "open_question_count", "risk_count", "a_grade_evidence_count",
    "only_market_discussion", "next_events_to_watch", "first_discovered_at",
    "last_updated_at",
}


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def test_root_endpoint_has_disclaimer(client):
    body = client.get("/").json()
    assert "disclaimer" in body
    assert "不构成投资建议" in body["disclaimer"]


def test_health_reports_selfcheck_and_degradation(client):
    body = client.get("/api/health").json()
    data = body["data"]
    assert data["status"] in {"ok", "degraded"}
    assert data["db"] == "ok"
    assert data["registry_problems"] == []
    # ★ 不写死「谁被实现了」—— 那会让每实现一个策略都要改测试。
    #   这里守的是**两个来源必须一致**：health 报告的名单 == registry 的声明。
    from app.strategies import implemented_types, designed_types

    # health 按 **ThesisType 枚举顺序**返回（与 /api/strategies 一致），
    # 不是字母序 —— 这里比对集合，顺序由 test_strategies_endpoint 单独守。
    assert set(data["strategies"]["implemented"]) == {
        c.value for c in implemented_types()
    }, "health 报告的已实现策略与 registry 不一致"
    assert len(data["strategies"]["designed"]) == len(designed_types())
    assert ThesisType.RESTRUCTURING.value in data["strategies"]["implemented"]
    assert ThesisType.TURNAROUND.value in data["strategies"]["implemented"]
    assert data["llm"]["mode"] == "dev"
    assert data["ingest"]["dry_run"] is True
    # 交易日历不可用时必须显式告警（R5）
    assert "calendar_ok" in data["scheduler"]
    if not data["scheduler"]["calendar_ok"]:
        assert data["scheduler"]["calendar_warning"]


def test_openapi_is_available(client):
    schema = client.get("/openapi.json").json()
    assert "/api/health" in schema["paths"]
    assert "/api/radar" in schema["paths"]
    assert "/api/opportunities/{opportunity_id}/score-breakdown" in schema["paths"]


# --------------------------------------------------------------------------- #
# Radar 首页
# --------------------------------------------------------------------------- #
def test_radar_shape(client, seeded):
    data = client.get("/api/radar").json()["data"]
    assert set(data) >= {"profile", "today", "counts", "recent_events", "alerts",
                         "pipeline", "disclaimer"}
    assert "disclaimer" in data

    profile = data["profile"]
    assert profile["top_weights"], "画像权重必须可用于展示（规格 §18）"
    assert profile["top_weights"][0]["thesis_type"] == "restructuring"

    today = data["today"]
    assert today["new_count"] >= 0
    assert isinstance(today["cards"], list)
    assert today["cards"], "首页应该有机会卡"
    # ★ M10-08：首页不得以 K 线 / 行情为主体
    assert "market" not in today
    assert "kline" not in today
    assert "price" not in today


def test_radar_card_contract(client, seeded):
    card = client.get("/api/radar").json()["data"]["today"]["cards"][0]
    assert REQUIRED_CARD_FIELDS <= set(card), REQUIRED_CARD_FIELDS - set(card)
    assert card["company"]["name"] == "ST XXX"
    assert card["thesis_display_name"] == "重组预期"
    assert card["status_label"] == "待确认"
    assert card["match_score"] == pytest.approx(94.0)
    assert card["rule_score"] == pytest.approx(67.5875)
    # ★ §17：卡片必须回答「为什么进入你的关注池」
    assert card["why_in_radar"]
    assert card["latest_events"]


# --------------------------------------------------------------------------- #
# 机会详情
# --------------------------------------------------------------------------- #
def test_opportunity_detail_answers_the_seven_questions(client, seeded):
    """规格第 1 节列出的 7 个问题必须在详情页结构里全部有落点。"""
    data = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    assert set(data) >= {"card", "thesis", "open_questions", "confirmed_facts",
                         "risks", "next_events_to_watch", "timeline", "evidence",
                         "market", "status_history", "disclaimer"}

    # 1 发生了什么 → timeline / latest_events
    assert data["timeline"]
    # 2 为什么重要 → thesis.statement
    assert data["thesis"]["statement"]
    # 3 为什么与我有关 → card.match_score
    assert data["card"]["match_score"] is not None
    # 4 有什么证据 → evidence（含原文段落定位）
    assert data["evidence"]
    assert data["evidence"][0]["relevant_text"]
    assert data["evidence"][0]["page"] is not None
    assert data["evidence"][0]["para_index"] is not None
    # 5 哪些地方还不确定 → open_questions（具体事项，而不是「待确认」标签）
    questions = [q["question"] for q in data["open_questions"]]
    assert "交易标的" in questions
    assert all(len(q) > 1 for q in questions)
    # 6 风险是什么 → risks
    assert data["risks"]
    # 7 下一步看什么 → next_events_to_watch
    assert "重组方案公告" in data["next_events_to_watch"]


def test_thesis_declares_invalidation_conditions(client, seeded):
    """★ M5-03：每个 Thesis 必须能展示「什么情况下这个逻辑不再成立」。"""
    data = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    invalidating = data["thesis"]["invalidating_events"]
    assert invalidating
    assert any("终止" in item["description"] for item in invalidating)
    assert all(item["severity"] in {"terminal", "severe", "warning"} for item in invalidating)


def test_why_now_is_present(client, seeded):
    why_now = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}"
    ).json()["data"]["thesis"]["why_now"]
    assert set(why_now) >= {"past", "recent", "this_week", "conclusion"}


def test_market_is_labelled_as_auxiliary(client, seeded):
    """规格 §46：行情数据属于辅助信息层，必须显式标注。"""
    data = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    assert "辅助信息" in data["market"]["note"]


# --------------------------------------------------------------------------- #
# 可解释性核心接口
# --------------------------------------------------------------------------- #
def test_score_breakdown_two_level_expansion(client, seeded):
    data = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}/score-breakdown"
    ).json()["data"]

    assert "disclaimer" in data
    assert data["rule_score"] == pytest.approx(67.5875)
    assert data["semantic_score"] == pytest.approx(76.0)
    assert data["risk_score"] == pytest.approx(40.75)

    dimensions = {d["dimension"]: d for d in data["dimensions"]}
    assert "thesis_match" in dimensions
    assert "risk" in dimensions

    # 第 1 级：维度分
    assert dimensions["thesis_match"]["display_name"] == "逻辑匹配"
    assert dimensions["thesis_match"]["raw_value"] == pytest.approx(94.0)
    assert dimensions["thesis_match"]["direction"] == "positive"

    # 第 2 级：逐项加减分 + 规则 ID + 证据绑定
    event_items = dimensions["event_catalyst"]["items"]
    assert event_items
    assert event_items[0]["rule_id"] == "R-GEN-EV-01"
    assert event_items[0]["evidence_ids"], "评分项必须绑定证据（规格 §13）"


def test_risk_dimension_has_direction_note(client, seeded):
    """★ 风险维度必须显式标注方向，否则用户会把「风险 43」读反。"""
    data = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}/score-breakdown"
    ).json()["data"]
    risk = next(d for d in data["dimensions"] if d["dimension"] == "risk")
    assert risk["direction"] == "negative"
    assert "越高代表风险越大" in risk["direction_note"]
    assert risk["weight"] < 0


def test_score_sum_matches_rule_score(client, seeded):
    """拆解必须自洽：各维度加权值之和 = 规则分。"""
    data = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}/score-breakdown"
    ).json()["data"]
    total = sum(d["weighted_value"] for d in data["dimensions"])
    assert total == pytest.approx(data["rule_score"], abs=1e-6)


def test_missing_breakdown_is_explicit_not_silent(client, seeded, session):
    """没有评分拆解时必须明说原因，而不是展示一个无法解释的分数。"""
    from sqlmodel import delete

    from app.models.opportunity import OpportunityScore

    session.exec(delete(OpportunityScore))
    session.commit()

    data = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}/score-breakdown"
    ).json()["data"]
    assert data["dimensions"] == []
    assert "尚未生成评分拆解" in data["note"]


# --------------------------------------------------------------------------- #
# 列表 / 反馈 / 画像 / 策略
# --------------------------------------------------------------------------- #
def test_opportunities_list_with_pagination(client, seeded):
    body = client.get("/api/opportunities?limit=10&offset=0").json()
    assert body["page"]["total"] >= 1
    assert body["meta"]["disclaimer"]
    assert body["data"][0]["id"] == seeded["opportunity_id"]


def test_opportunities_list_rejects_unknown_status(client, seeded):
    """坏输入必须显式报错，而不是静默返回空列表。"""
    response = client.get("/api/opportunities?status=not_a_status")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RULE_VIOLATION"
    assert "allowed" in response.json()["error"]["detail"]

    response = client.get("/api/opportunities?thesis_type=not_a_thesis")
    assert response.status_code == 422

    response = client.get("/api/events?event_type=NOT_A_TYPE")
    assert response.status_code == 422


def test_action_records_feedback(client, seeded):
    response = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "confirmed", "reason_thesis_types": ["restructuring"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["action"] == "confirmed"
    assert data["new_status"] == "tracking"


def test_action_rejects_unknown_action(client, seeded):
    response = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "definitely_buy"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RULE_VIOLATION"


def test_not_found_returns_404_envelope(client):
    response = client.get("/api/opportunities/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OPPORTUNITY_NOT_FOUND"


def test_profile_has_no_risk_appetite_tiers(client):
    """规格 §4.1：不使用「保守 / 稳健 / 激进」三档分类。"""
    data = client.get("/api/profile").json()["data"]
    assert "risk_tolerance" not in data or data.get("risk_tolerance") is None
    assert set(data) >= {"markets", "horizon", "exclusions", "locked_weights",
                         "auto_learn_enabled", "available_templates"}
    # ★ 不写死模板数量：新增模板（如「全景均衡」）不该让这条测试失败。
    #   这里守的是「模板列表来自 registry 且非空」。
    from app.strategies.registry import STRATEGY_TEMPLATES

    assert set(data["available_templates"]) == set(STRATEGY_TEMPLATES)
    assert data["available_templates"], "至少要有一个可用模板"


def test_profile_weights_lists_all_ten_strategies(client):
    data = client.get("/api/profile/weights").json()["data"]
    assert len(data["weights"]) == 10
    assert "ai_suggested" in data
    assert "归一化" in data["normalized_note"]


def test_profile_template_application(client):
    response = client.put("/api/profile/weights", json={"template": "重组猎手"})
    assert response.status_code == 200
    assert response.json()["data"]["applied_template"] == "重组猎手"


def test_profile_template_rejects_unknown(client):
    response = client.put("/api/profile/weights", json={"template": "不存在的模板"})
    assert response.status_code == 422


def test_locked_weight_blocks_learned_but_not_manual(client):
    """INV-P1 的精确语义：**自动学习**不得覆盖锁定项，但用户可以手动推翻自己的锁定。

    （M1-06：必须允许用户手动覆盖 AI 学习结果。）
    """
    client.put("/api/profile", json={"locked_weights": ["restructuring"]})

    # 1) 自动学习尝试覆盖锁定项 → 拒绝
    learned = client.put(
        "/api/profile/weights",
        json={"weights": [{"thesis_type": "restructuring", "weight": 0.99,
                           "source": "learned"}]},
    )
    assert learned.status_code == 422
    assert "INV-P1" in learned.json()["error"]["message"]

    # 2) 用户手动修改 → 允许（用户有权推翻自己的锁定）
    manual = client.put(
        "/api/profile/weights",
        json={"weights": [{"thesis_type": "restructuring", "weight": 0.50,
                           "source": "manual"}]},
    )
    assert manual.status_code == 200
    assert manual.json()["data"]["weights"]["restructuring"] == pytest.approx(0.50)

    # 3) 显式解锁
    unlocked = client.put(
        "/api/profile/weights",
        json={"weights": [{"thesis_type": "restructuring", "weight": 0.60,
                           "locked": False, "source": "learned"}]},
    )
    assert unlocked.status_code == 200
    assert "restructuring" not in unlocked.json()["data"]["locked_weights"]


def test_strategies_endpoint_design_then_implement(client):
    data = client.get("/api/strategies").json()["data"]
    assert len(data) == 10
    by_code = {s["code"]: s for s in data}
    from app.strategies import implemented_types

    expected_implemented = {c.value for c in implemented_types()}
    assert {c for c, s in by_code.items() if s["status"] == "implemented"} == (
        expected_implemented
    ), "接口报的实现状态与 registry 不一致"
    assert by_code["restructuring"]["status"] == "implemented"
    assert by_code["turnaround"]["status"] == "implemented"
    for strategy in data:
        assert strategy["invalidating_event_types"], f"{strategy['code']} 缺少失效条件"
        assert strategy["anti_patterns"], f"{strategy['code']} 缺少反例警示"
        assert sum(strategy["default_weights"].values()) == pytest.approx(0.95)


def test_strategies_share_of_positive_weight_is_consistent(client):
    data = client.get("/api/strategies").json()["data"]
    for strategy in data:
        assert "risk" not in strategy["default_weights"], (
            "风险必须是扣分项，不在正向权重表内"
        )


def test_parse_nl_requires_confirmation(client):
    """M1-04：必须先复述并向用户确认，再执行扫描。"""
    response = client.post(
        "/api/strategies/parse-nl",
        json={"text": "我想找那些连续亏损，但是最近出现重组、资产注入或者大股东变化迹象的 ST 股票。"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["requires_confirmation"] is True
    structured = data["structured"]
    assert "ST" in structured["stock_tags"]
    assert "连续亏损" in structured["financial_conditions"]
    assert "RESTRUCTURING" in structured["events"]
    assert "ASSET_INJECTION" in structured["events"]
    assert structured["weights"]
    assert "我理解你的投资策略为" in data["interpretation"]


def test_parse_nl_rejects_empty(client):
    response = client.post("/api/strategies/parse-nl", json={"text": "   "})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 事件与提醒
# --------------------------------------------------------------------------- #
def test_events_endpoint_shape(client, seeded):
    body = client.get("/api/events").json()
    assert body["data"]
    event = body["data"][0]
    assert event["company"]["name"] == "ST XXX"
    assert event["event_type_label"] == "重大资产重组"
    assert event["freshness"] in {"new", "updated", "breaking", "stale"}
    # ★ 规格 §40：必须区分事件发生时间与系统发现时间
    assert event["event_time"] and event["discovery_time"]
    assert "time_note" in event


def test_events_filter_by_invalidating(client, seeded):
    assert client.get("/api/events?is_invalidating=true").json()["data"] == []
    assert client.get("/api/events?is_invalidating=false").json()["data"]


def test_alerts_endpoints(client, seeded):
    assert client.get("/api/alerts").json()["data"] == []
    assert client.post("/api/alerts/read-all").json()["data"]["updated"] == 0
    assert client.post("/api/alerts/999/read").status_code == 404


# --------------------------------------------------------------------------- #
# My Thesis
# --------------------------------------------------------------------------- #
def test_my_thesis_aggregates_by_logic(client, seeded):
    data = client.get("/api/thesis").json()["data"]
    assert data["total_tracking"] >= 1
    assert data["by_type"]
    item = data["items"][0]
    # ★ 规格 §21：保存的是「因为 X 逻辑，所以关注 Y」
    assert item["statement"].startswith("公司")
    assert item["invalidating_events"]
    assert "disclaimer" in data


# --------------------------------------------------------------------------- #
# 管理端可观测
# --------------------------------------------------------------------------- #
def test_admin_runs_and_llm_runs_are_exposed(client):
    assert client.get("/api/admin/runs").json()["data"] == []

    body = client.get("/api/admin/llm-runs").json()
    assert "schema_failure_rate" in body["meta"]
    assert "extractor_verdict" in body["meta"]
    assert "R3" in body["meta"]["note"]


def test_admin_run_errors_404(client):
    assert client.get("/api/admin/runs/12345/errors").status_code == 404


# --------------------------------------------------------------------------- #
# 证据的「跳转原文」必须带页码锚点
# --------------------------------------------------------------------------- #
def test_evidence_deep_link_contains_page_anchor(client, seeded):
    """★ 实测反馈：「跳转原文」跳的都是错的。

    排查后确认：链接指向的**文档是对的**（HTTP 200、有效 PDF），
    但**没有页码锚点** —— 点进去永远停在 PDF 第 1 页。
    而重整/重组公告动辄几十上百页，用户在文档里根本找不到被引用那句话，
    「跳转原文」等于失效。
    """
    evidence = client.get(
        f"/api/opportunities/{seeded['opportunity_id']}/evidence"
    ).json()["data"]["supporting"]

    assert evidence, "该机会应有支撑证据"
    item = evidence[0]
    assert item["source_url"], "必须有原始链接"
    assert item["source_deep_link"], "必须提供带锚点的深链"
    assert item["page"] is not None

    assert item["source_deep_link"].endswith(f"#page={item['page']}"), (
        f"深链必须带页码锚点：{item['source_deep_link']}"
    )
    assert item["source_deep_link"].startswith(item["source_url"]), (
        "深链必须由原始链接派生，不能是另一个地址"
    )
    # 页码与段号都要能展示，方便用户手动核对
    assert item["para_index"] is not None


def test_deep_link_degrades_safely():
    """缺页码或 URL 已含锚点时必须原样返回，不能拼出坏链接。"""
    from app.api.serializers import deep_link

    class _Fake:
        source_url = "http://x/a.pdf"
        page = None

    assert deep_link(_Fake()) == "http://x/a.pdf"

    class _FakeWithAnchor:
        source_url = "http://x/a.pdf#page=3"
        page = 5

    assert deep_link(_FakeWithAnchor()) == "http://x/a.pdf#page=3"

    class _NoUrl:
        source_url = ""
        page = 2

    assert deep_link(_NoUrl()) == ""


# --------------------------------------------------------------------------- #
# 「今日机会」不得包含归档 / 失效的卡片
# --------------------------------------------------------------------------- #
def test_radar_cards_exclude_archived_and_invalidated(client, seeded, session):
    """★ 归档与失效的卡不得出现在「今日机会」里。

    为什么不能靠「反正它们分数低排不上」：实测归档/失效卡的分数可能**很高**
    —— 东兴/信达的失效判定被修正后，分数从 21 升到 43.5 / 42.75，
    直接排到全库第一、第二位。靠分数天然过滤是巧合，不是设计。

    它们仍然可达：``/api/opportunities?status=archived`` 能查到，
    也在 ``counts`` 里可见；失效另有**提醒**通道（规格 §23）。
    """
    from app.api.radar import ACTIVE_STATUSES
    from app.models.opportunity import Opportunity

    card = session.get(Opportunity, seeded["opportunity_id"])
    assert card is not None

    # 把种子卡推到一个非活跃状态，并给它一个足够高的分数让它「本该」排第一
    card.status = "archived"
    card.rule_score = 99.9
    session.add(card)
    session.commit()

    data = client.get("/api/radar").json()["data"]
    ids = [c["id"] for c in data["today"]["cards"]]
    assert seeded["opportunity_id"] not in ids, "归档的卡出现在了今日机会里"

    # 但它必须仍然可查（不能是「藏起来」）
    found = client.get("/api/opportunities?status=archived").json()["data"]
    items = found["items"] if isinstance(found, dict) else found
    assert any(item["id"] == seeded["opportunity_id"] for item in items), (
        "归档的卡在 /api/opportunities 里也查不到了 —— 这不是过滤，是丢失"
    )

    # counts 里也要如实反映
    assert data["counts"].get("archived", 0) >= 1

    # 活跃状态清单本身不得包含终态
    assert "archived" not in {s.value for s in ACTIVE_STATUSES}
    assert "invalidated" not in {s.value for s in ACTIVE_STATUSES}


# --------------------------------------------------------------------------- #
# 采集触发接口（界面的「运行扫描」按钮）
# --------------------------------------------------------------------------- #
def test_admin_ingest_uses_the_requested_source(client):
    """★ 这个端点原先**硬编码 `source="mock"`**。

    也就是说「从界面触发扫描」会**悄悄塞入一批假数据**，
    而且返回的报告看起来完全正常。这类「看起来能用、实际做错事」的接口
    比直接报错危险得多。

    这里断言请求参数被真正传递（用 mock 源跑一次，报告里的 source 必须是 mock；
    若换 cninfo，则不应出现 mock 的造数痕迹）。
    """
    response = client.post("/api/admin/ingest", json={
        "stage": "full", "source": "mock", "live": False, "limit": 4,
    })
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["stage"] == "full"
    # mock 源会造出固定的 4 家公司 —— 说明 source 参数生效了
    assert report["funnel"]["candidates"] == 4, report["funnel"]


def test_admin_ingest_defaults_to_real_source(client):
    """默认必须是**真实数据源**（cninfo），不是 mock。"""
    from app.api.admin import IngestRequest

    request = IngestRequest()
    assert request.source == "cninfo", "默认源若是 mock，界面上的「扫描」会写入假数据"
    assert request.pool == "market"
    assert request.live is False, "默认应当 dry-run（不写业务数据）"


def test_admin_ingest_reports_cards_and_created(client):
    """返回里要能看出「这次跑出了什么」—— 一个只回漏斗的报告无法回答按钮的效果。"""
    payload = client.post("/api/admin/ingest", json={
        "stage": "full", "source": "mock", "live": False,
    }).json()
    assert "cards" in payload["meta"]
    assert "created" in payload["meta"]
