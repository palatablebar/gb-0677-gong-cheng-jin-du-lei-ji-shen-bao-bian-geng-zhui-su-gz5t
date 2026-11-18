from flask import Blueprint, jsonify, request, current_app
from app.config import ROOT
from app.errors import DomainError
from app.visa import rules, store
from app.visa.archive import build_archive

bp = Blueprint("visa", __name__)

# story：月末复检未结；updated：同一申报批 + 复检结论已更新
SAMPLES = {"story": ("visa_declarations.csv", "visa_inspections.csv"),
           "updated": ("visa_declarations.csv", "visa_inspections_updated.csv")}


def _sample_file(name):
    path = ROOT / "sample_data" / name
    if not path.is_file(): raise DomainError("BAD_SAMPLE", "样例不存在", 404)
    return path.read_bytes()


def _execute(decl_raw, insp_raw, period, params):
    run = rules.evaluate(decl_raw, insp_raw, period, params)
    return {**run, "stored": store.save_run(run["run_id"], run)}


@bp.post("/api/visa/runs")
def create_run():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict): raise DomainError("BAD_BODY", "请求需要对象")
    sample = payload.get("sample", "story")
    if sample not in SAMPLES: raise DomainError("BAD_SAMPLE", "样例不存在", 404)
    decl = payload.get("declarations_csv")
    insp = payload.get("inspections_csv")
    if decl is not None and not isinstance(decl, str): raise DomainError("BAD_CSV", "CSV需要文本")
    if insp is not None and not isinstance(insp, str): raise DomainError("BAD_CSV", "CSV需要文本")
    period = payload.get("period") or None
    if period is not None and not isinstance(period, str): raise DomainError("BAD_PERIOD", "期间需要文本")
    params = payload.get("parameters", {})
    if not isinstance(params, dict): raise DomainError("BAD_PARAMETERS", "参数需要对象")
    decl_raw = decl.encode() if decl is not None else _sample_file(SAMPLES[sample][0])
    insp_raw = insp.encode() if insp is not None else _sample_file(SAMPLES[sample][1])
    return jsonify(_execute(decl_raw, insp_raw, period, params))


@bp.get("/api/visa/runs")
def runs():
    return {"runs": store.history()}


@bp.get("/api/visa/runs/<run_id>")
def run_detail(run_id):
    return jsonify(store.get_run(run_id))


@bp.post("/api/visa/runs/<run_id>/issue")
def issue(run_id):
    return jsonify(store.issue(run_id))


@bp.get("/api/visa/certificates")
def certs():
    return {"certificates": store.certificates(request.args.get("period") or None)}


@bp.get("/api/visa/certificates/current")
def current_cert():
    period = request.args.get("period")
    if not period: raise DomainError("BAD_PERIOD", "请提供期间")
    return {"certificate": store.current(period)}


@bp.get("/api/visa/runs/<run_id>/archive")
def archive(run_id):
    run = store.get_run(run_id)
    return current_app.response_class(build_archive(run), mimetype="text/csv",
                                      headers={"Content-Disposition": 'attachment; filename="visa_evidence.csv"'})


@bp.post("/api/visa/upload")
def upload():
    decl = request.files.get("declarations")
    insp = request.files.get("inspections")
    if not decl or not insp: raise DomainError("NO_FILE", "请同时选择申报记录和检验记录")
    return jsonify(_execute(decl.read(), insp.read(), request.form.get("period") or None, {}))
