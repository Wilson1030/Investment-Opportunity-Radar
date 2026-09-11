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

#: 合并换行时，单块的最大长度（超过则强制断开，避免整篇变成一段）
MAX_BLOCK_CHARS = 420

#: 页码 / 页眉页脚样式的噪声行
_NOISE = re.compile(
    r"^\s*(第?\s*\d+\s*页|共\s*\d+\s*页|证券代码[:：]|证券简称[:：]|公告编号[:：]|\d{1,3})\s*$"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。；！？])")

#: 句末标点 —— 行尾出现它才认为这一句说完了（可以断开）
_SENTENCE_END = re.compile(r"[。！？；：”』】）)]\s*$")

#: 小标题样式（「一、」「1.」「（2）」开头）—— 它们本身就是块边界
_HEADING = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、.]|\d+[、.．]|（\d+）|\(\d+\))")

#: 文种标题行（「关于……的公告 / 通知 / 报告 / 说明」）—— 独立成块：
#: 这类标题不含句末标点，若按常规合并会与正文粘在一起；
#: 而它本身常常是判断事件类型最直接、最可引用的一行。
_TITLE_LINE = re.compile(r"^[^。！？；]{4,40}?(?:公告|通知|报告书|报告|说明书|说明|提示性公告|进展公告)$")

#: 页眉元信息行（证券代码 / 证券简称 / 公告编号）—— 整行丢弃：
#: 它不含证据价值，却会污染证据片段（让人以为引用的是正文）
_HEADER_META = re.compile(
    r"^\s*(?:证券代码|证券简称|股票代码|股票简称|公告编号|债券代码|债券简称)\s*[:：]"
)


def join_wrapped_lines(text: str) -> list[str]:
    """把被硬换行切断的行回接成完整句块。

    **为什么必须做**：cninfo 的公告 PDF 多为分栏 / 文本框排版，
    ``extract_text()`` 出来的是大量短行。若按 ``\n`` 直接切段，会得到
    「组管理办法》规定的重大资产重组」这种碎片 —— 子串校验能过，
    但人没法读，证据链的核对价值就没了。

    规则：累积行直到「行尾是句末标点」或「块长超过上限」或「遇到小标题」。
    """
    blocks: list[str] = []
    buffer = ""

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or _NOISE.match(line) or _HEADER_META.match(line):
            if buffer:
                blocks.append(buffer)
                buffer = ""
            continue

        # 文种标题独立成块
        if _TITLE_LINE.match(line):
            if buffer:
                blocks.append(buffer)
            blocks.append(line)
            buffer = ""
            continue

        # 小标题自成一块：它本身不含证据价值（通常短于 MIN_PARAGRAPH_CHARS 会被丢弃），
        # 但必须断开，否则会与后面的正文粘成「三、风险提示本次交易尚需……」这种别扭的段落
        if _HEADING.match(line):
            if buffer:
                blocks.append(buffer)
            blocks.append(line)
            buffer = ""
            continue

        buffer = f"{buffer}{line}" if buffer else line

        if _SENTENCE_END.search(line) or len(buffer) >= MAX_BLOCK_CHARS:
            blocks.append(buffer)
            buffer = ""

    if buffer:
        blocks.append(buffer)

    # 单行就可能超过上限（PDF 有时把整段吐成一行）——必须在这里也断开，
    # 否则「整篇变成一段」，后续按句切分也救不回来
    bounded: list[str] = []
    for block in blocks:
        if len(block) <= MAX_BLOCK_CHARS:
            bounded.append(block)
            continue
        for piece in _SENTENCE_SPLIT.split(block):
            piece = piece.strip()
            if not piece:
                continue
            # 仍然过长（整段没有句末标点）→ 硬切，保证任何块都可读
            for offset in range(0, len(piece), MAX_BLOCK_CHARS):
                bounded.append(piece[offset : offset + MAX_BLOCK_CHARS])
    return bounded


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

    # ① 先把被硬换行切断的行回接成完整句块（PDF 分栏排版的必要处理）
    for block in join_wrapped_lines(text or ""):
        # ② 仍然过长的块按句号再切（保持单段可读）
        chunks = [block] if len(block) <= MAX_BLOCK_CHARS else [
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
    "MAX_BLOCK_CHARS",
    "MIN_PARAGRAPH_CHARS",
    "join_wrapped_lines",
    "parse_html",
    "parse_pdf",
    "split_text_to_paragraphs",
]
