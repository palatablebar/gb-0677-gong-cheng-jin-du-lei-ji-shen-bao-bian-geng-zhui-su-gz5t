import csv, io
import pytest
from app.config import ROOT
from app.errors import DomainError
from app.visa import rules, store

DECL = "decl_id,material,batch_no,item,period,qty,unit,amount,note\n"
INSP = "insp_id,material,batch_no,conclusion,inspected_at,note\n"


def decl_csv(rows): return (DECL + rows).encode()
def insp_csv(rows): return (INSP + rows).encode()
def load(name): return (ROOT / "sample_data" / name).read_bytes()
def story_run(): return rules.evaluate(load("visa_declarations.csv"), load("visa_inspections.csv"), "2026-11")


@pytest.fixture(autouse=True)
def isolated_visa_database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)


@pytest.fixture
def client():
    from app.main import create_app
    return create_app().test_client()


# ---------- 材料叫法归一与冻结 ----------

def test_alias_freeze_catches_different_names():
    "TRAP-31 材料名称与内部编码叫法不同也不能漏掉冻结"
    run = rules.evaluate(decl_csv("D1,HRB400螺纹钢,B1,基础清单,2026-11,10,t,42000.00,配筋\n"),
                         insp_csv("I1,钢筋,B1,复检中,2026-11-12T14:00:00+08:00,复检\n"), "2026-11")
    line = run["lines"][0]
    assert line["canonical"] == "M-REBAR" and line["status"] == "冻结" and line["insp_id"] == "I1"


def test_alias_pass_with_different_names():
    run = rules.evaluate(decl_csv("D1,C30,B2,基础清单,2026-11,10,m³,4500.00,浇筑\n"),
                         insp_csv("I1,砼,B2,合格,2026-11-12T14:00:00+08:00,\n"), "2026-11")
    assert run["lines"][0]["status"] == "可签认"


def test_extra_aliases_via_parameters():
    run = rules.evaluate(decl_csv("D1,减水剂,B1,基础清单,2026-11,1,t,5000.00,外加剂\n"),
                         insp_csv("I1,减水剂,B1,合格,2026-11-12T14:00:00+08:00,\n"),
                         "2026-11", {"aliases": {"M-ADMIXTURE": ["减水剂", "外加剂"]}})
    assert run["lines"][0]["canonical"] == "M-ADMIXTURE" and run["lines"][0]["status"] == "可签认"


# ---------- 本期可签认范围与对平 ----------

def test_story_scope_balanced():
    run = story_run()
    m = run["metrics"]
    assert m["lines"] == {"declared": 8, "signable": 3, "frozen": 4, "rejected": 1}
    assert m["amount"] == {"declared": "719700.00", "signable": "262800.00",
                           "frozen": "432900.00", "rejected": "24000.00"}
    assert m["qty"]["t"] == {"declared": "167", "signable": "80", "frozen": "87", "rejected": "0"}
    assert m["qty"]["m³"] == {"declared": "850", "signable": "500", "frozen": "150", "rejected": "200"}
    assert m["balanced"] is True and m["unresolved"] == 0 and m["issuable"] is True


def test_same_batch_two_items_frozen_together():
    "同一进场批供给两个清单，批级结论同时冻结两条申报"
    run = story_run()
    frozen = {l["decl_id"] for l in run["lines"] if l["status"] == "冻结"}
    assert {"D2603", "D2604"} <= frozen
    d3 = [l for l in run["lines"] if l["decl_id"] == "D2603"][0]
    assert d3["canonical"] == "M-REBAR" and d3["conclusion"] == "复检中"


def test_failed_inspection_rejected():
    run = story_run()
    d6 = [l for l in run["lines"] if l["decl_id"] == "D2606"][0]
    assert d6["status"] == "不予签认" and d6["conclusion"] == "不合格"


def test_no_inspection_record_is_frozen():
    "TRAP-35 无检验记录一律冻结，不得默认可签"
    run = story_run()
    d8 = [l for l in run["lines"] if l["decl_id"] == "D2608"][0]
    assert d8["status"] == "冻结" and d8["reason"] == "未检"


def test_orphan_inspection_listed_not_blocking():
    run = story_run()
    assert [u["insp_id"] for u in run["unmatched_inspections"]] == ["I9007"]
    assert run["metrics"]["issuable"] is True


def test_by_item_aggregation():
    run = story_run()
    items = {i["item"]: i for i in run["by_item"]}
    assert items["基础清单"]["signable_amount"] == "262800.00"
    assert items["基础清单"]["frozen_amount"] == "189000.00"
    assert items["主体结构清单"]["frozen_lines"] == 3


# ---------- 检验结论变化 ----------

def test_later_conclusion_supersedes():
    insp = INSP + "I1,钢筋,B1,待检,2026-11-10T14:00:00+08:00,\nI2,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"
    run = rules.evaluate(decl_csv("D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\n"), insp.encode(), "2026-11")
    assert run["lines"][0]["status"] == "可签认" and run["lines"][0]["insp_id"] == "I2"


def test_inspection_conflict_freezes_batch():
    "TRAP-32 同一批同时刻结论冲突，冻结并阻断签发"
    insp = INSP + "I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\nI2,钢筋,B1,不合格,2026-11-12T14:00:00+08:00,\n"
    run = rules.evaluate(decl_csv("D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\n"), insp.encode(), "2026-11")
    line = run["lines"][0]
    assert line["status"] == "冻结" and line["reason"] == "检验冲突"
    assert run["metrics"]["unresolved"] == 1 and run["metrics"]["issuable"] is False


def test_updated_scope_amounts():
    run = rules.evaluate(load("visa_declarations.csv"), load("visa_inspections_updated.csv"), "2026-11")
    m = run["metrics"]
    assert m["amount"]["signable"] == "569700.00" and m["amount"]["frozen"] == "126000.00"
    assert m["lines"]["frozen"] == 1 and m["balanced"] is True and m["issuable"] is True


# ---------- 签发、升版与幂等 ----------

def test_issue_and_idempotent_replay(client):
    "TRAP-34 重跑同一批不能重复放行"
    run = client.post("/api/visa/runs", json={"sample": "story"}).get_json()
    c1 = client.post(f"/api/visa/runs/{run['run_id']}/issue").get_json()
    assert c1["version"] == 1 and c1["status"] == "现行" and c1["idempotent_replay"] is False
    assert c1["signable_amount"] == "262800.00"
    c2 = client.post(f"/api/visa/runs/{run['run_id']}/issue").get_json()
    assert c2["certificate_id"] == c1["certificate_id"] and c2["version"] == 1
    assert c2["idempotent_replay"] is True
    certs = client.get("/api/visa/certificates?period=2026-11").get_json()["certificates"]
    assert len(certs) == 1


def test_conclusion_change_updates_current_certificate(client):
    "TRAP-33 检验结论变化后更新现行签证"
    r1 = client.post("/api/visa/runs", json={"sample": "story"}).get_json()
    c1 = client.post(f"/api/visa/runs/{r1['run_id']}/issue").get_json()
    r2 = client.post("/api/visa/runs", json={"sample": "updated"}).get_json()
    assert r2["run_id"] != r1["run_id"]
    c2 = client.post(f"/api/visa/runs/{r2['run_id']}/issue").get_json()
    assert c2["version"] == 2 and c2["status"] == "现行"
    assert set(c2["released"]) > set(c1["released"])
    assert len(c2["released"]) == len(set(c2["released"]))
    cur = client.get("/api/visa/certificates/current?period=2026-11").get_json()["certificate"]
    assert cur["certificate_id"] == c2["certificate_id"]
    certs = client.get("/api/visa/certificates?period=2026-11").get_json()["certificates"]
    old = [c for c in certs if c["version"] == 1][0]
    assert old["status"] == "已更新"


def test_rerun_same_batch_reuses_run(client):
    a = client.post("/api/visa/runs", json={"sample": "story"}).get_json()
    b = client.post("/api/visa/runs", json={"sample": "story"}).get_json()
    assert a["run_id"] == b["run_id"] and b["stored"] is False
    assert len(client.get("/api/visa/runs").get_json()["runs"]) == 1


def test_issue_blocked_when_unresolved(client):
    run = client.post("/api/visa/runs", json={
        "declarations_csv": DECL + "D1,减水剂,B1,基础清单,2026-11,1,t,5000.00,外加剂\n",
        "inspections_csv": INSP + "I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n",
        "period": "2026-11"}).get_json()
    assert run["quarantine"][0]["reason"] == "UNKNOWN_MATERIAL"
    assert run["metrics"]["issuable"] is False
    resp = client.post(f"/api/visa/runs/{run['run_id']}/issue")
    assert resp.status_code == 422 and resp.get_json()["code"] == "UNRESOLVED"


# ---------- 入口纪律 ----------

def test_inspection_time_requires_timezone():
    insp = INSP + "I1,钢筋,B1,合格,2026-11-12 14:00:00,\n"
    run = rules.evaluate(decl_csv("D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\n"), insp.encode(), "2026-11")
    assert run["inspection_quarantine"][0]["reason"] == "INVALID_TIME"
    assert run["lines"][0]["status"] == "冻结" and run["lines"][0]["reason"] == "未检"


def test_duplicate_declaration_conflict():
    d = DECL + "D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\nD1,钢筋,B1,基础清单,2026-11,11,t,42000.00,y\n"
    run = rules.evaluate(d.encode(), insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"), "2026-11")
    assert run["quarantine"][0]["reason"] == "DUPLICATE_CONFLICT"
    assert run["metrics"]["lines"]["declared"] == 1


def test_exact_duplicate_counts_once():
    d = DECL + "D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\nD1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\n"
    run = rules.evaluate(d.encode(), insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"), "2026-11")
    assert run["metrics"]["declarations"]["duplicate"] == 1
    assert run["metrics"]["lines"]["declared"] == 1


def test_wrong_period_quarantined():
    run = rules.evaluate(decl_csv("D1,钢筋,B1,基础清单,2026-10,10,t,42000.00,x\n"),
                         insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"), "2026-11")
    assert run["quarantine"][0]["reason"] == "WRONG_PERIOD"
    assert run["metrics"]["issuable"] is False


def test_ambiguous_period_rejected():
    d = DECL + "D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,x\nD2,钢筋,B2,基础清单,2026-12,10,t,42000.00,x\n"
    with pytest.raises(DomainError) as e:
        rules.evaluate(d.encode(), insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"))
    assert e.value.code == "AMBIGUOUS_PERIOD"


def test_empty_states():
    for raw, code in [(b"", "EMPTY_FILE"), (b"\n\r\n", "BLANK_ONLY"), (DECL.encode(), "HEADER_ONLY")]:
        with pytest.raises(DomainError) as e:
            rules.evaluate(raw, insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"))
        assert e.value.code == code


def test_legacy_encoding_declarations():
    d = DECL + "D1,钢筋,B1,基础清单,2026-11,10,t,42000.00,配筋\n"
    run = rules.evaluate(d.encode("gb18030"),
                         insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"), "2026-11")
    assert run["metrics"]["declarations"]["encoding"] == "gb18030"
    assert run["lines"][0]["status"] == "可签认"


def test_bad_header_rejected():
    with pytest.raises(DomainError) as e:
        rules.evaluate(b"a,b,c\n1,2,3\n", insp_csv("I1,钢筋,B1,合格,2026-11-12T14:00:00+08:00,\n"))
    assert e.value.code == "BAD_HEADER"


# ---------- 接口与页面 ----------

def test_run_detail_404(client):
    assert client.get("/api/visa/runs/missing").status_code == 404


def test_issue_404(client):
    assert client.post("/api/visa/runs/missing/issue").status_code == 404


def test_bad_sample_404(client):
    assert client.post("/api/visa/runs", json={"sample": "nope"}).status_code == 404


def test_archive_export(client):
    run = client.post("/api/visa/runs", json={"sample": "story"}).get_json()
    r = client.get(f"/api/visa/runs/{run['run_id']}/archive")
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    kinds = {x[3] for x in rows[1:]}
    assert rows[0][0] == "run_id"
    assert {"signable", "frozen", "rejected", "unmatched_inspection"} <= kinds


def test_upload_endpoint(client):
    data = {"declarations": (io.BytesIO(load("visa_declarations.csv")), "d.csv"),
            "inspections": (io.BytesIO(load("visa_inspections.csv")), "i.csv")}
    r = client.post("/api/visa/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["metrics"]["issuable"] is True


def test_upload_requires_both_files(client):
    data = {"declarations": (io.BytesIO(load("visa_declarations.csv")), "d.csv")}
    assert client.post("/api/visa/upload", data=data, content_type="multipart/form-data").status_code == 422


def test_visa_page(client):
    r = client.get("/visa")
    assert r.status_code == 200 and "月末签证工作台" in r.get_data(as_text=True)
