"""文档解析与段落切分（D10：证据链落到原文段落）。

产出 1-based 的 ``(page, para_index)`` —— 这是 ``Evidence`` 能「点开定位原文」的前提，
也是证据闸门第 2 道校验（``relevant_text`` 必须是段落子串）的比对基准。

依赖 ``pdfplumber``（可选安装：``pip install -e ".[ingest]"``）。
扫描件（无文字层）会被标记为 ``needs_ocr`` 而不是伪造内容 —— 见 D10 的降级路径。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ingest.base import ParsedDocument
from app.models.enums import ParseStatus

#: 段落切分的最小长度（过短的碎片对证据链无意义）
MIN_PARAGRAPH_CHARS = 12

#: 页码 / 页眉页脚样式的噪声行
_NOISE = re.compile(
    r"^\s*(第?\s*\d+\s*页|共\s*\d+\s*页|证券代码[:：]|证券简称[:：]|公告编号[:：]|\d{1,3})\s*$"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。；！？])")


def split_text_to_paragraphs(
    text: str, page: int, start_index: int = 1
) -> list[tuple[int, int, str, int, int]]:
    """把一页文本切成段落。

    策略：先按换行切，过长的块再按句号切，过滤噪声行与过短片段。
    返回 ``(page, para_index, text, char_start, char_end)``。
    """
    paragraphs: list[tuple[int, int, str, int, int]] = []
    cursor = 0
    index = start_index

    for raw_block in re.split(r"\n{1,}", text or ""):
        block = raw_block.strip()
        if not block or _NOISE.match(block):
            continue
        chunks = [block] if len(block) <= 220 else [
            c.strip() for c in _SENTENCE_SPLIT.split(block) if c.strip()
        ]
        for chunk in chunks:
            if len(chunk) < MIN_PARAGRAPH_CHARS:
                continue
            paragraphs.append((page, index, chunk, cursor, cursor + len(chunk)))
            cursor += len(chunk)
            index += 1
    return paragraphs


def parse_pdf(path: str | Path) -> ParsedDocument:
    """解析 PDF。"""
    try:
        import pdfplumber  # 延迟导入：未安装采集依赖时不影响核心功能
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 pdfplumber，请执行：pip install -e \".[ingest]\""
        ) from exc

    path = Path(path)
    paragraphs: list[tuple[int, int, str, int, int]] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                texts.append(page_text)
                paragraphs.extend(
                    split_text_to_paragraphs(page_text, page_number, start_index=1)
                )
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都要如实上报，不能吞掉
        return ParsedDocument(
            fulltext="",
            paragraphs=(),
            parse_status=ParseStatus.FAILED.value,
            parse_error=f"{type(exc).__name__}: {exc}",
            raw_format="pdf",
        )

    fulltext = "\n".join(texts)
    if not paragraphs:
        # 有页面但提不出文字 → 扫描件（无文字层）。降级：存链接 + 标记 needs_ocr（D10）
        return ParsedDocument(
            fulltext=fulltext,
            paragraphs=(),
            parse_status=ParseStatus.NEEDS_OCR.value,
            parse_error="未提取到文字层，疑似扫描件；已降级为仅保存链接",
            raw_format="pdf",
        )

    status = ParseStatus.OK.value if len(paragraphs) >= 3 else ParseStatus.PARTIAL.value
    return ParsedDocument(
        fulltext=fulltext,
        paragraphs=tuple(paragraphs),
        parse_status=status,
        raw_format="pdf",
    )


def parse_html(html: str) -> ParsedDocument:
    """解析 HTML 全文（cninfo 的部分公告为 html）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - 无 bs4 时用正则兜底
        text = re.sub(r"<[^>]+>", "\n", html or "")
    else:
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n")

    paragraphs = split_text_to_paragraphs(text, page=1, start_index=1)
    if not paragraphs:
        return ParsedDocument(
            fulltext=text,
            paragraphs=(),
            parse_status=ParseStatus.NEEDS_OCR.value,
            parse_error="HTML 中未提取到有效段落",
            raw_format="html",
        )
    return ParsedDocument(
        fulltext=text,
        paragraphs=tuple(paragraphs),
        parse_status=ParseStatus.OK.value,
        raw_format="html",
    )


__all__ = [
    "MIN_PARAGRAPH_CHARS",
    "parse_html",
    "parse_pdf",
    "split_text_to_paragraphs",
]
