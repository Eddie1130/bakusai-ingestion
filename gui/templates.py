"""Editable prompt catalog and validation / 可編輯的提示詞範本."""

from __future__ import annotations

from analysis import prompt

MAX_TEMPLATE_CHARACTERS = 50_000

# Only these bundled resources may be edited. Filenames from HTTP requests are
# never passed to resource or filesystem loaders.
TEMPLATE_CATALOG = (
    (
        "report_spec.md",
        "已知名字・分析報告",
        "單次分析與兩段式第二輪使用，可調整報告結構、語氣與分析重點。",
        ("$store", "$girl", "$dimensions"),
    ),
    (
        "filter_spec.md",
        "已知名字・第一輪篩選",
        "兩段式第一輪使用。請保留輸出留言 ID 的要求，方便接續第二輪。",
        ("$store", "$girl", "$dimensions"),
    ),
    (
        "features_spec.md",
        "特徵尋人",
        "調整候選人的整理方式、比對條件與輸出格式。",
        ("$store",),
    ),
    (
        "roster_spec.md",
        "全店女生整理",
        "調整人物清單、別名、在籍說法與證據的整理方式。",
        ("$store",),
    ),
    (
        "dimensions.md",
        "共用・評估面向",
        "由已知名字範本中的 $dimensions 插入；可調整評估分類。",
        (),
    ),
    (
        "discovery_rules.md",
        "共用・證據規則",
        "附加在特徵尋人與全店整理的任務後方，設定兩種模式共用的證據要求。",
        (),
    ),
    (
        "custom_spec.md",
        "其他・自訂分析",
        "作為自訂分析的起始內容；也可在分析頁直接改寫本次要求。",
        ("$store",),
    ),
)
TEMPLATE_NAMES = frozenset(item[0] for item in TEMPLATE_CATALOG)


def catalog() -> dict:
    return {
        "templates": [
            {
                "name": name,
                "label": label,
                "description": description,
                "placeholders": placeholders,
                "default": prompt.load_template(name),
            }
            for name, label, description, placeholders in TEMPLATE_CATALOG
        ],
        "max_characters": MAX_TEMPLATE_CHARACTERS,
    }


def validate_overrides(value: object) -> dict[str, str]:
    """Copy validated overrides so a two-step session owns its own snapshot."""
    if not isinstance(value, dict) or any(key not in TEMPLATE_NAMES for key in value):
        raise ValueError("提示詞範本設定無效，請重新開啟範本編輯器")
    result = {}
    for name, text in value.items():
        if not isinstance(text, str) or not text.strip():
            raise ValueError("提示詞範本不可留空；可使用「還原預設」恢復內容")
        if len(text) > MAX_TEMPLATE_CHARACTERS:
            raise ValueError(f"每份提示詞範本最多 {MAX_TEMPLATE_CHARACTERS:,} 個字元")
        result[name] = text
    return result
