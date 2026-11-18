import json, sqlite3
from datetime import datetime, timezone
from app.config import DATA_DIR
from app.errors import DomainError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS visa_runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS visa_certificates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(period,run_id))""")
    return db


def save_run(run_id, payload):
    """同一批输入得到同一批次号，重跑只复用不重复写入。"""
    with connect() as db:
        cursor = db.execute("INSERT OR IGNORE INTO visa_runs(id,payload) VALUES (?,?)",
                            (run_id, json.dumps(payload, ensure_ascii=False)))
        return cursor.rowcount == 1


def get_run(run_id):
    with connect() as db:
        row = db.execute("SELECT payload FROM visa_runs WHERE id=?", (run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND", "对账批次不存在", 404)
    return json.loads(row[0])


def history():
    with connect() as db:
        rows = db.execute("SELECT id,payload FROM visa_runs ORDER BY rowid DESC").fetchall()
    out = []
    for run_id, payload in rows:
        data = json.loads(payload); m = data["metrics"]
        out.append({"run_id": run_id, "period": data["period"], "balanced": m["balanced"],
                    "issuable": m["issuable"], "unresolved": m["unresolved"],
                    "signable_amount": m["amount"]["signable"], "frozen_amount": m["amount"]["frozen"],
                    "generated_at": data["manifest"]["generated_at"]})
    return out


def issue(run_id):
    """签发正式签证：可签认范围对平且无待决事项才放行。

    同一范围重复签发返回原证（不重复放行）；检验结论变化形成新批次后，
    签发时本期现行签证升版，旧版标记已更新，放行的量只按现行证计一次。
    """
    run = get_run(run_id)
    m = run["metrics"]
    if not m["balanced"]: raise DomainError("UNBALANCED", "可签认范围未对平，不能形成正式签证")
    if m["unresolved"]: raise DomainError("UNRESOLVED", "存在未决隔离或检验冲突，不能形成正式签证")
    period = run["period"]
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        row = db.execute("SELECT payload FROM visa_certificates WHERE period=? AND run_id=?",
                         (period, run_id)).fetchone()
        if row: return {**json.loads(row[0]), "idempotent_replay": True}
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM visa_certificates WHERE period=?",
                             (period,)).fetchone()[0]
        db.execute("UPDATE visa_certificates SET status='已更新' WHERE period=? AND status='现行'", (period,))
        cert = {"certificate_id": f"VQ-{period}-{version:02d}", "period": period, "version": version,
                "status": "现行", "run_id": run_id,
                "signable_amount": m["amount"]["signable"], "frozen_amount": m["amount"]["frozen"],
                "rejected_amount": m["amount"]["rejected"],
                "signable_lines": m["lines"]["signable"], "frozen_lines": m["lines"]["frozen"],
                "rejected_lines": m["lines"]["rejected"],
                "released": [l["decl_id"] for l in run["lines"] if l["status"] == "可签认"],
                "by_item": run["by_item"], "rule_version": run["manifest"]["rule_version"],
                "issued_at": now}
        db.execute("INSERT INTO visa_certificates(period,run_id,version,status,payload,created_at) VALUES (?,?,?,?,?,?)",
                   (period, run_id, version, "现行", json.dumps(cert, ensure_ascii=False), now))
    return {**cert, "idempotent_replay": False}


def certificates(period=None):
    sql = "SELECT status,payload FROM visa_certificates"
    args = ()
    if period:
        sql += " WHERE period=?"; args = (period,)
    sql += " ORDER BY period,version DESC"
    with connect() as db:
        # payload是签发时快照，现行/已更新以状态列为准
        return [{**json.loads(p), "status": s} for s, p in db.execute(sql, args).fetchall()]


def current(period):
    with connect() as db:
        row = db.execute("SELECT status,payload FROM visa_certificates WHERE period=? AND status='现行'", (period,)).fetchone()
    return {**json.loads(row[1]), "status": row[0]} if row else None
