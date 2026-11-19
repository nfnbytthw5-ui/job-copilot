"""去重用的归一化规则。规则固定、可测试，不做模糊匹配。"""

from __future__ import annotations

import re
import unicodedata

_COMPANY_SUFFIXES = [
    "股份有限公司", "有限责任公司", "集团有限公司", "有限公司", "集团", "公司",
]
_TITLE_PREFIXES = [
    "高级", "资深", "初级", "senior", "junior", "sr.", "sr", "jr.",
]


def _to_halfwidth(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _strip_brackets(text: str) -> str:
    # 中英文括号及其内容一并去掉（公司名里的英文名、股票代码都在里面）
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    return text


def normalize_company(raw: str) -> str:
    if not raw:
        return ""
    text = _to_halfwidth(raw).strip()
    text = _strip_brackets(text)
    text = re.sub(r"\s+", "", text)

    # 「分公司」是独立用工主体，必须保留
    is_branch = text.endswith("分公司")
    if is_branch:
        text = text[: -len("分公司")]

    # 主体后缀可能出现在中间（「北京汽车股份有限公司株洲」），按任意位置删除
    for suffix in _COMPANY_SUFFIXES:
        text = text.replace(suffix, "")

    if is_branch:
        text += "分公司"

    text = text.replace("·", "").replace("-", "").replace("_", "")
    return text.lower()


def normalize_title(raw: str) -> str:
    if not raw:
        return ""
    text = _to_halfwidth(raw).strip()
    text = _strip_brackets(text)
    text = re.sub(r"\s+", "", text)

    lowered = text.lower()
    for prefix in _TITLE_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
            break

    # 去掉薪资尾巴
    text = re.sub(r"\d+\s*[-~]\s*\d+\s*[kK千万]", "", text)
    text = text.replace("/", "·")
    return text.lower()


def normalize_city(raw: str) -> str:
    if not raw:
        return ""
    text = _to_halfwidth(raw).strip()
    text = re.sub(r"\s+", "", text)
    # 只保留到市级：去掉「市」「区」「县」
    text = re.sub(r"(市|区|县)$", "", text)
    return text.lower()


def jd_hash(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
