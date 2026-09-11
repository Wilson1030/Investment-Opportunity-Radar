"""PDF 段落切分回归（D10 证据链的地基）。

**为什么这个测试很重要**：抽样核对时发现，cninfo 的公告 PDF 多为分栏 / 文本框排版，
``extract_text()`` 返回大量被硬换行的短行。若按 ``\\n`` 直接切段，会得到

    ✗ 「组管理办法》规定的重大资产重组」
    ✗ 「本次交易不涉及发行股份，不构成关联交易，也不会」

子串校验仍然能过（它确实是原文的子串），但**人没法读**——
「每个判断都能点开原文段落核对」这个核心承诺就落空了。
"""

from __future__ import annotations

import pytest

from app.ingest.parcel import (
    MAX_BLOCK_CHARS,
    MIN_PARAGRAPH_CHARS,
    join_wrapped_lines,
    parse_html,
    split_text_to_paragraphs,
)

#: 模拟 cninfo PDF 的真实排版：元信息行 + 标题 + 小标题 + 被硬换行切断的正文
WRAPPED_PDF_TEXT = """证券代码：600725  证券简称：云维股份  公告编号：2026-045
关于重大资产重组进展的公告
本公司董事会及全体董事保证本公告内容不存在任何虚假记载。
一、本次交易概述
云南云维股份有限公司（以下简称“云维股份”）拟通过发行股份及支付现金
方式购买云南省能源投资集团有限公司持有的云南能投威信能源有限公司
和云南能投红河发电有限公司）（以下简称“标的公司”）100%股权（以下
简称“本次交易”）。
本次交易预计将构成《上市公司重大资产重组管理办法》规定的重大资产重组。
三、风险提示
本次交易尚需提交公司股东大会审议，能否获得批准存在不确定性。
12
"""


def test_wrapped_lines_are_joined_into_complete_sentences():
    """★ 核心回归：句子不得被硬换行切断。"""
    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    joined = "\n".join(paragraphs)

    # 这些**段落**都不得出现（它们是 PDF 换行被当成段落边界的结果）。
    # 注意：只判断「段落」而不是「joined 子串」——
    # 合并之后这些字节串当然会作为完整句子的一部分出现，那不是问题。
    assert not any(text.startswith("组管理办法") for text in paragraphs)
    assert not any(text.endswith("（以下") for text in paragraphs)
    assert not any(text.endswith("也不会") for text in paragraphs)
    assert not any(text.endswith("及支付现金") for text in paragraphs)

    # 文种标题必须独立成块（否则会与下一行粘成「……的公告本公司董事会……」）
    assert "关于重大资产重组进展的公告" in paragraphs

    # 完整句子必须出现
    assert any(
        "本次交易预计将构成《上市公司重大资产重组管理办法》规定的重大资产重组。" in text
        for text in paragraphs
    )
    assert any(
        text.startswith("云南云维股份有限公司") and text.endswith("（以下简称“本次交易”）。")
        for text in paragraphs
    )


def test_headings_are_separated_from_body():
    """小标题不得与正文粘成「三、风险提示本次交易尚需……」这种别扭的段落。"""
    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    assert not any("风险提示本次交易尚需" in text for text in paragraphs)
    assert not any("本次交易概述云南云维" in text for text in paragraphs)
    assert any(text.startswith("本次交易尚需提交公司股东大会审议") for text in paragraphs)


def test_header_metadata_lines_are_dropped():
    """页眉元信息（证券代码 / 公告编号）不得进入证据片段。"""
    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    assert not any("证券代码" in text for text in paragraphs)
    assert not any("公告编号" in text for text in paragraphs)
    # 文种标题必须独立成块并保留 —— 它常常是判断事件类型最直接的证据
    assert "关于重大资产重组进展的公告" in paragraphs


def test_page_numbers_are_dropped():
    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    assert not any(text.strip() == "12" for text in paragraphs)


def test_paragraphs_are_numbered_from_one_and_ordered():
    paragraphs = split_text_to_paragraphs(WRAPPED_PDF_TEXT, page=2)
    assert paragraphs, "必须切出段落"
    indexes = [para_index for _, para_index, _, _, _ in paragraphs]
    assert indexes == list(range(1, len(paragraphs) + 1)), "段落号必须从 1 连续"
    assert all(page == 2 for page, _, _, _, _ in paragraphs)
    # 偏移量单调递增（用于前端定位）
    starts = [start for _, _, _, start, _ in paragraphs]
    assert starts == sorted(starts)


def test_short_fragments_are_discarded():
    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    assert all(len(text) >= MIN_PARAGRAPH_CHARS for text in paragraphs)


def test_join_wrapped_lines_respects_block_size_cap():
    """没有句末标点的长文本也必须断开，否则整篇会变成一段。"""
    single_line = "公司拟进行重大资产重组" * 60          # 无任何句末标点
    blocks = join_wrapped_lines(single_line)
    assert len(blocks) >= 1
    assert max(len(block) for block in blocks) <= MAX_BLOCK_CHARS


def test_join_wrapped_lines_breaks_on_blank_lines():
    blocks = join_wrapped_lines("第一段内容。\n\n第二段内容。")
    assert blocks == ["第一段内容。", "第二段内容。"]


def test_substring_invariant_holds_after_joining():
    """★ 合并换行后，INV-E1 仍然成立：引用片段必须是段落子串。"""
    from app.engine import guard

    paragraphs = [text for _, _, text, _, _ in split_text_to_paragraphs(WRAPPED_PDF_TEXT, 1)]
    target = next(t for t in paragraphs if t.startswith("本次交易预计将构成"))
    # 取值本身必须匹配
    assert guard.relevance_substring_match(target, target)
    # 跨段落拼接出来的「句子」不得被当成合法证据
    other = next(t for t in paragraphs if t.startswith("本次交易尚需"))
    assert not guard.relevance_substring_match(target + other, target)


def test_html_parsing_also_joins_lines():
    html = "<html><body><p>公司拟以发行股份及支付现金</p><p>方式购买标的资产。</p></body></html>"
    parsed = parse_html(html)
    assert parsed.parse_status in {"ok", "partial"}
    assert parsed.paragraphs, "HTML 也应切出段落"
    assert any("方式购买标的资产。" in text for _, _, text, _, _ in parsed.paragraphs)


def test_empty_input_is_not_an_error():
    assert split_text_to_paragraphs("", 1) == []
    assert join_wrapped_lines("") == []
    assert parse_html("").paragraphs == ()
