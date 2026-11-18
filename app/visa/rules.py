import csv, hashlib, io, json, time, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from app.errors import DomainError
from app.reasons import reject
from app.values import number, money, instant
from app.modules.m1_claims.rules import decode
from app.visa.aliases import canonical_material

DECL_COLUMNS = ["decl_id", "material", "batch_no", "item", "period", "qty", "unit", "amount", "note"]
INSP_COLUMNS = ["insp_id", "material", "batch_no", "conclusion", "inspected_at", "note"]
RULE_VERSION = "visa-2026.11.18-r1"
ZERO = Decimal(0)

# 检验结论归一：合格放行；待检/复检中冻结；不合格不予签认
CONCLUSIONS = {"合格": "合格", "通过": "合格", "PASS": "合格",
               "待检": "待检", "未检": "待检", "PENDING": "待检",
               "复检中": "复检中", "复检": "复检中", "RECHECK": "复检中",
               "不合格": "不合格", "FAIL": "不合格", "FAILED": "不合格"}
UNIT_ALIASES = {"M3": "m³", "立方米": "m³", "立方": "m³", "吨": "t", "T": "t", "KG": "kg", "千克": "kg"}


def _norm(text):
    return unicodedata.normalize("NFKC", str(text)).strip()


_CONCLUSION_LOOKUP = {_norm(k).upper(): v for k, v in CONCLUSIONS.items()}


def _intake(raw, columns, kind):
    """与申报采信一致的入口纪律：编码回退、空态三态、列数隔离、同键去重、冲突隔离。"""
    text, encoding = decode(raw)
    if not text: raise DomainError("EMPTY_FILE", f"{kind}文件为空")
    if not text.strip(): raise DomainError("BLANK_ONLY", f"{kind}全部为空行")
    stream = csv.reader(io.StringIO(text, newline=""))
    header = next(stream, None)
    if header != columns: raise DomainError("BAD_HEADER", f"{kind}表头不匹配")
    rows = []; bad = []; seen = {}
    duplicate = blank = repeated = count = 0
    try:
        for cells in stream:
            count += 1
            line = stream.line_num
            if not cells or not any(c.strip() for c in cells): blank += 1; continue
            if cells == header: repeated += 1; continue
            row = {"_line": line, "_sources": [f"{kind}:{line}"]}
            if len(cells) != len(columns): bad.append(reject(row, "BAD_COLUMNS", "列数不匹配")); continue
            values = [x.strip() for x in cells]
            row.update(dict(zip(columns, values))); row["_raw"] = values
            key = row[columns[0]]
            if not key: bad.append(reject(row, "EMPTY_VALUE", "业务键为空")); continue
            if key in seen:
                if values == seen[key]["_raw"]: duplicate += 1
                else: bad.append(reject(row, "DUPLICATE_CONFLICT", "同键不同值"))
                continue
            seen[key] = row; rows.append(row)
    except csv.Error: raise DomainError("BAD_CSV", f"{kind}引号未闭合或文件损坏")
    if count == 0: raise DomainError("HEADER_ONLY", f"{kind}仅有表头")
    if not rows and not bad: raise DomainError("BLANK_ONLY", f"{kind}全部为空行")
    metrics = {"input": count, "accepted": len(rows), "quarantine": len(bad), "duplicate": duplicate,
               "blank": blank, "repeated_headers": repeated, "encoding": encoding}
    return rows, bad, metrics


def _valid_period(text):
    if len(text) != 7: return False
    try:
        datetime.strptime(text, "%Y-%m")
        return True
    except ValueError:
        return False


def _prepare_declarations(rows, extra):
    good = []; bad = []
    for row in rows:
        try:
            canonical = canonical_material(row["material"], extra)
            if canonical is None: raise DomainError("UNKNOWN_MATERIAL", "材料叫法未登记，先行冻结")
            qty = number(row["qty"])
            if qty <= 0: raise DomainError("BAD_QTY", "申报数量必须为正")
            amount = number(row["amount"])
            if not _valid_period(row["period"]): raise DomainError("BAD_PERIOD", "期间格式应为YYYY-MM")
            unit = _norm(row["unit"])
            if not unit: raise DomainError("EMPTY_VALUE", "单位为空")
            unit = UNIT_ALIASES.get(unit.upper(), unit)
            if not row["batch_no"]: raise DomainError("EMPTY_VALUE", "进场批号为空")
            if not row["item"]: raise DomainError("EMPTY_VALUE", "清单项为空")
            good.append({**row, "canonical": canonical, "qty": str(qty), "amount": money(amount), "unit": unit})
        except DomainError as e:
            bad.append(reject(row, e.code, e.message))
    return good, bad


def _prepare_inspections(rows, extra):
    good = []; bad = []
    for row in rows:
        try:
            canonical = canonical_material(row["material"], extra)
            if canonical is None: raise DomainError("UNKNOWN_MATERIAL", "材料叫法未登记")
            conclusion = _CONCLUSION_LOOKUP.get(_norm(row["conclusion"]).upper())
            if conclusion is None: raise DomainError("BAD_CONCLUSION", "检验结论无法识别")
            if not row["batch_no"]: raise DomainError("EMPTY_VALUE", "进场批号为空")
            ts = instant(row["inspected_at"])
            good.append({**row, "canonical": canonical, "conclusion": conclusion, "inspected_at": ts.isoformat()})
        except DomainError as e:
            bad.append(reject(row, e.code, e.message))
    return good, bad


def _derive_period(decls):
    periods = {d["period"] for d in decls}
    if not periods: raise DomainError("NO_PERIOD", "没有可确定的申报期间")
    if len(periods) > 1: raise DomainError("AMBIGUOUS_PERIOD", "申报记录跨多个期间，请指定period")
    return periods.pop()


def _reconcile(decls, insps):
    """逐条对账：申报行按(规范材料,进场批)取最新检验结论。

    合格->可签认；待检/复检中->冻结；不合格->不予签认；无记录->冻结(未检)；
    同一批同时刻结论冲突->冻结(检验冲突)。检验结论变化后重跑即按最新结论重判。
    """
    groups = defaultdict(list)
    for r in insps: groups[(r["canonical"], r["batch_no"])].append(r)
    latest = {}; conflicts = set()
    for key, rs in groups.items():
        top = max(r["inspected_at"] for r in rs)
        tops = [r for r in rs if r["inspected_at"] == top]
        if len({r["conclusion"] for r in tops}) > 1: conflicts.add(key)
        else: latest[key] = tops[0]
    referenced = {(d["canonical"], d["batch_no"]) for d in decls}
    unmatched = [latest[k] for k in sorted(latest) if k not in referenced]
    lines = []
    for d in decls:
        key = (d["canonical"], d["batch_no"])
        if key in conflicts:
            lines.append({**d, "status": "冻结", "conclusion": "检验冲突", "insp_id": "", "reason": "检验冲突"})
        elif key not in latest:
            lines.append({**d, "status": "冻结", "conclusion": "未检", "insp_id": "", "reason": "未检"})
        else:
            r = latest[key]; c = r["conclusion"]
            if c == "合格":
                lines.append({**d, "status": "可签认", "conclusion": c, "insp_id": r["insp_id"], "reason": ""})
            elif c == "不合格":
                lines.append({**d, "status": "不予签认", "conclusion": c, "insp_id": r["insp_id"], "reason": "不合格"})
            else:
                lines.append({**d, "status": "冻结", "conclusion": c, "insp_id": r["insp_id"], "reason": c})
    return lines, unmatched, conflicts


def _by_item(lines):
    items = {}
    key_of = {"可签认": "signable", "冻结": "frozen", "不予签认": "rejected"}
    for l in lines:
        it = items.setdefault(l["item"], {"item": l["item"], "lines": 0, "signable_lines": 0, "frozen_lines": 0,
                                          "rejected_lines": 0, "declared_amount": ZERO, "signable_amount": ZERO,
                                          "frozen_amount": ZERO, "rejected_amount": ZERO})
        it["lines"] += 1
        it["declared_amount"] += Decimal(l["amount"])
        k = key_of[l["status"]]
        it[f"{k}_lines"] += 1
        it[f"{k}_amount"] += Decimal(l["amount"])
    out = []
    for it in items.values():
        out.append({k: (money(v) if k.endswith("_amount") else v) for k, v in it.items()})
    return sorted(out, key=lambda x: x["item"])


def _metrics(lines, decl_bad, insp_bad, unmatched, conflicts, decl_meta, insp_meta, period):
    signable = [l for l in lines if l["status"] == "可签认"]
    frozen = [l for l in lines if l["status"] == "冻结"]
    rejected = [l for l in lines if l["status"] == "不予签认"]

    def qty(rows, u): return sum((Decimal(r["qty"]) for r in rows if r["unit"] == u), ZERO)
    def amt(rows): return sum((Decimal(r["amount"]) for r in rows), ZERO)

    units = sorted({l["unit"] for l in lines})
    qty_block = {}
    for u in units:
        q = {"declared": qty(lines, u), "signable": qty(signable, u), "frozen": qty(frozen, u), "rejected": qty(rejected, u)}
        qty_block[u] = {k: str(v) for k, v in q.items()}
    amounts = {"declared": amt(lines), "signable": amt(signable), "frozen": amt(frozen), "rejected": amt(rejected)}
    # 对平：每条申报必须且只能落入一个桶，数量与金额两侧分别守恒
    ids = [l["decl_id"] for l in lines]
    partition = len(ids) == len(set(ids)) == len(signable) + len(frozen) + len(rejected)
    qty_ok = all(Decimal(qty_block[u]["declared"]) == Decimal(qty_block[u]["signable"]) +
                 Decimal(qty_block[u]["frozen"]) + Decimal(qty_block[u]["rejected"]) for u in units)
    amount_ok = amounts["declared"] == amounts["signable"] + amounts["frozen"] + amounts["rejected"]
    balanced = partition and qty_ok and amount_ok
    unresolved = len(decl_bad) + len(insp_bad) + len(conflicts)
    return {
        "period": period,
        "declarations": decl_meta, "inspections": insp_meta,
        "lines": {"declared": len(lines), "signable": len(signable), "frozen": len(frozen), "rejected": len(rejected)},
        "amount": {k: money(v) for k, v in amounts.items()},
        "qty": qty_block,
        "unmatched_inspections": len(unmatched),
        "conflicts": len(conflicts),
        "unresolved": unresolved,
        "balanced": balanced,
        "issuable": balanced and unresolved == 0,
    }


def _digest(decl_raw, insp_raw, period, parameters):
    h = hashlib.sha256()
    h.update(decl_raw); h.update(b"\x00"); h.update(insp_raw); h.update(b"\x00")
    h.update(period.encode())
    h.update(json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode())
    h.update(RULE_VERSION.encode())
    return h.hexdigest()[:20]


def evaluate(decl_raw, insp_raw, period=None, parameters=None):
    """月末签证对账：申报记录×检验记录 -> 本期可签认范围。纯函数，不写库。"""
    started = time.monotonic()
    parameters = parameters or {}
    extra = parameters.get("aliases")
    if extra is not None and (not isinstance(extra, dict) or
                              any(not isinstance(k, str) or not isinstance(v, list) for k, v in extra.items())):
        raise DomainError("BAD_PARAMETERS", "aliases需要{编码:[叫法...]}")
    decl_rows, decl_bad, decl_meta = _intake(decl_raw, DECL_COLUMNS, "申报记录")
    insp_rows, insp_bad, insp_meta = _intake(insp_raw, INSP_COLUMNS, "检验记录")
    decls, bad = _prepare_declarations(decl_rows, extra); decl_bad += bad
    insps, bad = _prepare_inspections(insp_rows, extra); insp_bad += bad
    if period is not None and not _valid_period(period):
        raise DomainError("BAD_PERIOD", "期间格式应为YYYY-MM")
    period = period or _derive_period(decls)
    scoped = []
    for d in decls:
        if d["period"] != period:
            decl_bad.append(reject(d, "WRONG_PERIOD", f"申报期间{d['period']}不属于本期{period}"))
        else:
            scoped.append(d)
    lines, unmatched, conflicts = _reconcile(scoped, insps)
    decl_meta["quarantine"] = len(decl_bad); insp_meta["quarantine"] = len(insp_bad)
    metrics = _metrics(lines, decl_bad, insp_bad, unmatched, conflicts, decl_meta, insp_meta, period)
    manifest = {"declarations_sha256": hashlib.sha256(decl_raw).hexdigest(),
                "inspections_sha256": hashlib.sha256(insp_raw).hexdigest(),
                "rule_version": RULE_VERSION, "parameters": parameters,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}
    return {"run_id": _digest(decl_raw, insp_raw, period, parameters), "period": period,
            "lines": lines, "quarantine": decl_bad, "inspection_quarantine": insp_bad,
            "unmatched_inspections": unmatched,
            "conflicts": sorted(f"{c}/{b}" for c, b in conflicts),
            "by_item": _by_item(lines), "metrics": metrics, "manifest": manifest}
