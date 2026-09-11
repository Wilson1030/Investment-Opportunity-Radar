"""采集层（可插拔数据源，D09）。

    base.py            适配器协议与结果类型
    cninfo.py          巨潮公告列表 + 全文（风险 R2 所在）
    akshare_source.py  ST 名单 / 交易日历 / 财务 / 新闻
    parcel.py          PDF/HTML → 段落切分（D10 证据链的基础）
    normalizer.py      清洗与幂等落库
    cli.py             python -m app.ingest
"""

__all__ = ["akshare_source", "base", "cli", "cninfo", "normalizer", "parcel"]
