"""生成 ``data/mock/`` 下的 10 个策略示例（规格 §5.6 的示例 A ~ J）。

用途（docs/07 §4.2）：
* **回归用例** —— 每个示例对应一类策略，验证「事件 → Thesis → 匹配 → 评分」链路
* 其中 ``example_j`` 是最重要的一条：**非 ST 公司也能命中困境反转**，
  验证规格 §5.6 的「策略决定为什么被发现，而不是标签决定策略」

运行：python tools/build_mock_data.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "mock"

T0 = "2026-09-08T11:32:00+00:00"

EXAMPLES: dict[str, dict] = {
    "example_a": {
        "label": "重组预期型（规格示例 A）",
        "thesis_type": "restructuring",
        "expect": {"status": "pending_confirmation", "coverage": 0.94, "match_score": 94.0,
                   "rule_score": 67.5875},
        "profile_weights": {"restructuring": 0.40, "ma_integration": 0.25, "turnaround": 0.15},
        "company": {"name": "ST XXX", "code": "600xxx", "is_st": True,
                    "industry": "机械设备"},
        "financials": {"loss_years": 2, "ocf_positive": True},
        "shareholder": {"controlling_shareholder_changed": True},
        "market": {"abnormal_volatility": True, "social_buzz": True},
        "open_question_count": 6,
        "has_unanswered_inquiry": True,
        "has_history_failure": True,
        "evidence_levels": ["A", "A", "A"],
        "newest_evidence_age_days": 0.5,
        "events": [
            {"event_type": "RESTRUCTURING", "title": "重大资产重组预案公告",
             "event_time": T0, "evidence_level": "A", "amount_ratio": 0.20,
             "counterparty_known": True},
            {"event_type": "CONTROL_CHANGE", "title": "关于控股股东变更的公告",
             "event_time": T0, "evidence_level": "A"},
            {"event_type": "ASSET_INJECTION", "title": "关于资产注入的进展公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_b": {
        "label": "业绩拐点型（规格示例 B）",
        "thesis_type": "turnaround",
        "expect": {"status": "tracking"},
        "profile_weights": {"turnaround": 0.40, "restructuring": 0.25},
        "company": {"name": "公司 B", "code": "000bbb", "is_st": False},
        "financials": {"loss_years": 2, "revenue_improving_quarters": 2,
                       "margin_improving_quarters": 2, "ocf_positive": True,
                       "ocf_improving": True},
        "open_question_count": 4,
        "evidence_levels": ["B", "B"],
        "newest_evidence_age_days": 1.0,
        "events": [
            {"event_type": "EARNINGS_TURNAROUND", "title": "2026 年半年度业绩预告",
             "event_time": T0, "evidence_level": "B"},
            {"event_type": "MAJOR_CONTRACT", "title": "关于签订重大合同的公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_c": {
        "label": "高股息 / 现金流型（规格示例 C）",
        "thesis_type": "value",
        "expect": {"note": "「事件」可以是分红+回购+现金流+估值的组合，无需重大新闻"},
        "profile_weights": {"value": 0.40, "shareholder_action": 0.20},
        "company": {"name": "公司 C", "code": "000ccc", "is_st": False},
        "financials": {"profitable_years": 8, "ocf_positive": True},
        "shareholder": {"buyback": True, "buyback_scale_significant": True},
        "valuation_percentile": 0.25,
        "open_question_count": 2,
        "evidence_levels": ["B"],
        "newest_evidence_age_days": 3.0,
        "events": [
            {"event_type": "DIVIDEND_POLICY", "title": "关于提高现金分红比例的公告",
             "event_time": T0, "evidence_level": "A"},
            {"event_type": "BUYBACK", "title": "关于回购公司股份方案的公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_d": {
        "label": "成长型（规格示例 D）",
        "thesis_type": "growth",
        "expect": {"note": "需要行业数据 / 订单 / 产能利用率 / 份额 —— 不是只看股价"},
        "profile_weights": {"growth": 0.40, "product": 0.25},
        "company": {"name": "公司 D", "code": "000ddd", "is_st": False},
        "financials": {"revenue_improving_quarters": 4, "profitable_years": 3,
                       "ocf_positive": True, "ocf_improving": True},
        "open_question_count": 3,
        "evidence_levels": ["B", "A"],
        "newest_evidence_age_days": 1.0,
        "events": [
            {"event_type": "MAJOR_CONTRACT", "title": "关于新签订单的自愿性披露公告",
             "event_time": T0, "evidence_level": "A", "amount_ratio": 0.35},
            {"event_type": "NEW_PRODUCT", "title": "关于新产品投产的公告",
             "event_time": T0, "evidence_level": "B"},
        ],
    },
    "example_e": {
        "label": "政策驱动型（规格示例 E）",
        "thesis_type": "policy",
        "expect": {"gating": "C5 未命中时 coverage 上限 0.45",
                   "note": "不能仅因「属于政策相关行业」就判定为机会"},
        # ★ 关键：只有政策与产业链，没有实际订单 → 必须被门控压住
        "core_condition_satisfaction": {"C1": 1.0, "C2": 1.0, "C3": 1.0,
                                        "C4": 0.6, "C5": 0.0},
        "coverage_cap": 0.45,
        "profile_weights": {"policy": 0.45, "growth": 0.25},
        "company": {"name": "公司 E", "code": "000eee", "is_st": False,
                    "industry": "新能源", "industry_chain": ["储能", "电池材料"]},
        "open_question_count": 4,
        "evidence_levels": ["A"],
        "newest_evidence_age_days": 2.0,
        "events": [
            {"event_type": "POLICY_CATALYST", "title": "关于某产业支持政策的通知",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_f": {
        "label": "并购 / 产业整合型（规格示例 F）",
        "thesis_type": "ma_integration",
        "expect": {"note": "「发生并购」≠ 好机会：必须同时分析 催化剂 + 交易质量 + 风险",
                   "open_questions": ["收购价格", "商誉风险", "业绩承诺", "整合难度"]},
        "profile_weights": {"ma_integration": 0.40, "restructuring": 0.20},
        "company": {"name": "公司 F", "code": "000fff", "is_st": False},
        "financials": {"profitable_years": 4, "ocf_positive": True},
        "open_question_count": 8,
        "evidence_levels": ["A"],
        "newest_evidence_age_days": 1.0,
        "events": [
            {"event_type": "M&A", "title": "关于收购同行业公司股权的公告",
             "event_time": T0, "evidence_level": "A", "amount_ratio": 0.45,
             "counterparty_known": True},
        ],
    },
    "example_g": {
        "label": "大股东增持 / 回购型（规格示例 G）",
        "thesis_type": "shareholder_action",
        "expect": {"note": "不能只做正向判断：必须检查资金来源 / 规模 / 质押 / 历史表现",
                   "risk_trigger": "hunt_risk 至少输出 3 项反向结论"},
        "profile_weights": {"shareholder_action": 0.35, "value": 0.30},
        "company": {"name": "公司 G", "code": "000ggg", "is_st": False},
        "financials": {"profitable_years": 5, "ocf_positive": True},
        "shareholder": {"insider_buy": True, "buyback": True,
                        "buyback_scale_significant": True, "high_pledge": True},
        "open_question_count": 4,
        "evidence_levels": ["A"],
        "newest_evidence_age_days": 0.5,
        "events": [
            {"event_type": "SHAREHOLDER_BUY", "title": "关于控股股东增持公司股份计划的公告",
             "event_time": T0, "evidence_level": "A"},
            {"event_type": "BUYBACK", "title": "关于回购公司股份的进展公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_h": {
        "label": "行业周期反转型（规格示例 H）",
        "thesis_type": "cycle",
        "expect": {"entry": "industry_first",
                   "note": "必须能「先发现行业机会，再向下寻找公司」"},
        "profile_weights": {"cycle": 0.40, "turnaround": 0.25},
        "company": {"name": "公司 H", "code": "000hhh", "is_st": False,
                    "industry": "化工", "industry_chain": ["基础化工", "氯碱"]},
        "financials": {"loss_years": 2, "ocf_positive": True},
        "open_question_count": 4,
        "evidence_levels": ["B"],
        "newest_evidence_age_days": 5.0,
        "events": [
            {"event_type": "OTHER", "title": "行业库存与开工率数据月度跟踪",
             "event_time": T0, "evidence_level": "B"},
            {"event_type": "EARNINGS_TURNAROUND", "title": "2026 年半年度业绩预告",
             "event_time": T0, "evidence_level": "B"},
        ],
    },
    "example_i": {
        "label": "技术 / 新产品突破型（规格示例 I）",
        "thesis_type": "product",
        "expect": {"gating": "C3（客户验证）未命中 → C4 / C5 一律计 0",
                   "note": "不能把「宣布研发成功」等同于「商业成功」"},
        # ★ 关键：只有研发与认证，没有客户验证 → 下游条件必须计 0
        "core_condition_satisfaction": {"C1": 1.0, "C2": 1.0, "C3": 0.0,
                                        "C4": 0.0, "C5": 0.0},
        "profile_weights": {"product": 0.35, "growth": 0.30},
        "company": {"name": "公司 I", "code": "000iii", "is_st": False},
        "financials": {"loss_years": 1},
        "open_question_count": 4,
        "evidence_levels": ["A"],
        "newest_evidence_age_days": 1.0,
        "events": [
            {"event_type": "NEW_PRODUCT", "title": "关于新产品获得认证的公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
    "example_j": {
        "label": "困境反转型，但不依赖 ST（规格示例 J）★ 最重要的一条回归",
        "thesis_type": "turnaround",
        "expect": {
            "coverage": 0.80,
            "is_st": False,
            "note": ("非 ST 公司也能命中困境反转 —— 验证「投资策略应该决定股票为什么被发现，"
                     "而不是股票标签决定投资策略」（规格 §5.6）"),
        },
        # ★ is_st=False，但事件与举措齐备 → coverage 仍然为 0.80
        "profile_weights": {"turnaround": 0.40, "cycle": 0.20},
        "company": {"name": "公司 J", "code": "000jjj", "is_st": False,
                    "industry": "建材"},
        "financials": {"loss_years": 2, "ocf_positive": True, "ocf_improving": True,
                       "margin_improving_quarters": 2, "revenue_improving_quarters": 2},
        "shareholder": {},
        "open_question_count": 3,
        "evidence_levels": ["B", "B"],
        "newest_evidence_age_days": 2.0,
        "events": [
            {"event_type": "EARNINGS_TURNAROUND", "title": "2026 年半年度业绩预告",
             "event_time": T0, "evidence_level": "B"},
            {"event_type": "M&A", "title": "关于剥离亏损子公司股权的公告",
             "event_time": T0, "evidence_level": "A"},
            {"event_type": "MANAGEMENT_CHANGE", "title": "关于聘任总经理的公告",
             "event_time": T0, "evidence_level": "A"},
        ],
    },
}


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, payload in EXAMPLES.items():
        path = TARGET / f"{name}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  ok  data/mock/{name}.json  —— {payload['label']}")

    index = {
        "generated_by": "tools/build_mock_data.py",
        "source": "产品设计规格 §5.6 示例 A ~ J",
        "purpose": [
            "端到端回归用例（docs/07 §4.2）",
            "example_j 验证：策略决定为什么被发现，而不是标签决定策略",
            "example_e / example_i 验证：顺序门控在数据层就无法被绕过",
        ],
        "examples": [
            {
                "file": f"{name}.json",
                "label": payload["label"],
                "thesis_type": payload["thesis_type"],
                "is_st": payload.get("company", {}).get("is_st", False),
                "expect": payload.get("expect", {}),
            }
            for name, payload in EXAMPLES.items()
        ],
        "note": "全部公司、代码与数据均为虚构示例，不构成任何投资建议。",
    }
    (TARGET / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  ok  data/mock/index.json（{len(EXAMPLES)} 个示例）")


if __name__ == "__main__":
    main()
