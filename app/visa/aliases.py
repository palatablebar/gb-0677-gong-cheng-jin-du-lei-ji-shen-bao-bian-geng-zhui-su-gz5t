import unicodedata

# 规范材料编码 -> 已知叫法（材料名称、商品名、牌号、内部编码、英文）
# 申报记录与检验记录对同一材料的叫法常常不同，先归一再匹配，保证冻结不漏。
ALIASES = {
    "M-REBAR": ["钢筋", "螺纹钢", "螺纹钢筋", "HRB400", "HRB400E", "HRB335", "HRB400螺纹钢", "001", "REBAR"],
    "M-CONCRETE": ["混凝土", "砼", "预拌混凝土", "C30", "C35", "C40", "002", "CONCRETE"],
    "M-CEMENT": ["水泥", "P.O42.5", "PO42.5", "PO425", "003", "CEMENT"],
    "M-SAND": ["砂", "中砂", "河砂", "细砂", "004", "SAND"],
}


def _normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().upper()
    return "".join(text.split())


_LOOKUP = {}
for _code, _names in ALIASES.items():
    for _name in _names:
        _LOOKUP[_normalize(_name)] = _code


def canonical_material(text, extra=None):
    """把材料叫法归一为规范编码；未登记的叫法返回None（调用方必须冻结，不得放行）。

    extra 允许按运行参数扩展别名表：{"M-ADMIXTURE": ["减水剂", "外加剂"]}。
    """
    lookup = _LOOKUP
    if extra:
        lookup = dict(_LOOKUP)
        for code, names in extra.items():
            for name in names:
                lookup[_normalize(name)] = code
    return lookup.get(_normalize(text))
