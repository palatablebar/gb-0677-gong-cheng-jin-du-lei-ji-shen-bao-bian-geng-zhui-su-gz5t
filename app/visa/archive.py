import csv, io, json


def build_archive(run):
    """唯一证据导出：逐条对账结果 + 隔离 + 未匹配检验，含来源行与结论。"""
    stream = io.StringIO(newline="")
    w = csv.writer(stream)
    w.writerow(["run_id", "period", "rule_version", "kind", "ref_id", "material", "canonical", "batch_no",
                "item", "qty", "unit", "amount", "conclusion", "status", "reason", "sources", "evidence"])
    rid = run["run_id"]; period = run["period"]; rv = run["manifest"]["rule_version"]
    kind_of = {"可签认": "signable", "冻结": "frozen", "不予签认": "rejected"}

    def sources(row): return json.dumps(row.get("_sources", []), ensure_ascii=False)
    def evidence(row): return json.dumps(row, ensure_ascii=False)

    for l in run["lines"]:
        w.writerow([rid, period, rv, kind_of[l["status"]], l["decl_id"], l["material"], l["canonical"],
                    l["batch_no"], l["item"], l["qty"], l["unit"], l["amount"], l["conclusion"],
                    l["status"], l["reason"], sources(l), evidence(l)])
    for q in run["quarantine"]:
        w.writerow([rid, period, rv, "quarantine", q.get("decl_id", ""), q.get("material", ""), "",
                    q.get("batch_no", ""), q.get("item", ""), q.get("qty", ""), q.get("unit", ""),
                    q.get("amount", ""), "", "隔离", q.get("reason", ""), sources(q), evidence(q)])
    for q in run["inspection_quarantine"]:
        w.writerow([rid, period, rv, "inspection_quarantine", q.get("insp_id", ""), q.get("material", ""), "",
                    q.get("batch_no", ""), "", "", "", "", "", "隔离", q.get("reason", ""), sources(q), evidence(q)])
    for u in run["unmatched_inspections"]:
        w.writerow([rid, period, rv, "unmatched_inspection", u["insp_id"], u["material"], u["canonical"],
                    u["batch_no"], "", "", "", "", u["conclusion"], "未匹配", "", sources(u), evidence(u)])
    return "﻿" + stream.getvalue()
