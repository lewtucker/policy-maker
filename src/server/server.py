"""
Policy Maker Server
FastAPI app with session auth, per-user SQLite storage, and policy evaluation.
"""
import os
import secrets
import tempfile
import yaml
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

import database
import user_engine
from policy_engine import PolicyEngine
from policy_analyzer import analyze, summarize
from nl_policy import create_chat_handler

_server_dir = Path(__file__).parent
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
APP_PASSWORD   = os.environ.get("APP_PASSWORD")
if not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD is not set. Add it to src/server/.env or the environment.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="Policy Maker", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="pm_session",
    max_age=86400 * 7,
)


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _require_session(request: Request) -> str:
    email = request.session.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return email


def _get_engine(email: str) -> PolicyEngine:
    """Return a PolicyEngine loaded from the user's current YAML (read-only use)."""
    engine, tmp = user_engine.get_engine(email)
    Path(tmp).unlink(missing_ok=True)
    return engine


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    if not request.session.get("email"):
        return RedirectResponse("/login")
    return FileResponse(_server_dir / "static" / "index.html")


@app.get("/login")
async def login_page(request: Request):
    if request.session.get("email"):
        return RedirectResponse("/")
    return FileResponse(_server_dir / "static" / "login.html")


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not email or password != APP_PASSWORD:
        return RedirectResponse("/login?error=1", status_code=303)
    database.get_or_create_user(email)
    request.session["email"] = email
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/me")
async def me(request: Request):
    email = _require_session(request)
    user = database.get_user(email)
    engine = _get_engine(email)
    return {"email": email, "rule_count": len(engine.rules), "created_at": user["created_at"]}


# ── Policy endpoints ──────────────────────────────────────────────────────────

@app.get("/policies")
async def list_policies(request: Request):
    email = _require_session(request)
    engine = _get_engine(email)
    return {"policies": [r.to_dict() for r in engine.rules]}


class RuleBody(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    result: str
    priority: int = 0
    protected: bool = False
    match: dict = {}


@app.post("/policies")
async def add_policy(body: RuleBody, request: Request):
    email = _require_session(request)
    engine, tmp = user_engine.get_engine(email)
    try:
        rule = engine.add(body.model_dump())
        user_engine.save_engine(email, tmp)
        return rule.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)


@app.put("/policies/{rule_id}")
async def update_policy(rule_id: str, body: RuleBody, request: Request):
    email = _require_session(request)
    engine, tmp = user_engine.get_engine(email)
    try:
        rule = engine.update(rule_id, body.model_dump())
        user_engine.save_engine(email, tmp)
        return rule.to_dict()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)


@app.delete("/policies/{rule_id}")
async def delete_policy(rule_id: str, request: Request):
    email = _require_session(request)
    engine, tmp = user_engine.get_engine(email)
    try:
        removed = engine.remove(rule_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
        user_engine.save_engine(email, tmp)
        return {"deleted": rule_id}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/policies/delete-all")
async def delete_all_policies(request: Request):
    email = _require_session(request)
    engine, tmp = user_engine.get_engine(email)
    try:
        to_delete = [r.id for r in engine.rules if not r.protected]
        for rule_id in to_delete:
            engine.remove(rule_id)
        user_engine.save_engine(email, tmp)
        return {"deleted": len(to_delete)}
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/policies/analyze")
async def analyze_policies(request: Request):
    email = _require_session(request)
    engine = _get_engine(email)
    findings = analyze(engine.rules, known_people=[], known_groups=[])
    return {
        "findings": [f.to_dict() for f in findings],
        "summary": summarize(findings),
    }


@app.post("/policies/import")
async def import_policies(request: Request, file: UploadFile = File(None)):
    email = _require_session(request)
    if file:
        content = (await file.read()).decode("utf-8")
    else:
        body = await request.body()
        content = body.decode("utf-8")
    try:
        count = user_engine.import_yaml(email, content)
        return {"imported": count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/policies/export")
async def export_policies(request: Request):
    email = _require_session(request)
    yaml_str = database.get_rules_yaml(email)
    return Response(
        content=yaml_str,
        media_type="text/yaml",
        headers={"Content-Disposition": "attachment; filename=policies.yaml"},
    )


# ── Simulate endpoint ─────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    tool: str = ""
    program: str = ""
    path: str = ""
    person: str = ""
    group: str = ""


@app.post("/simulate")
async def simulate(body: SimulateRequest, request: Request):
    email = _require_session(request)
    engine = _get_engine(email)

    # Build params dict matching what policy_engine._matches() expects
    params = {}
    if body.program:
        params["command"] = f"{body.program} ..."
    if body.path:
        params["path"] = body.path

    # Build a minimal subject-like object if person/group provided
    subject = None
    if body.person or body.group:
        class _Subject:
            def __init__(self, person_id, groups):
                self.id = person_id
                self.groups = groups
        subject = _Subject(body.person, [body.group] if body.group else [])

    # Run evaluation and collect the full trace
    trace = []
    first_match_found = False
    for rule in engine.rules:
        matched = engine._matches(rule, body.tool, params, subject)
        fired = matched and not first_match_found
        if fired:
            first_match_found = True

        # Build per-condition breakdown
        conditions = _explain_conditions(rule, body, subject)
        trace.append({
            "rule": rule.to_dict(),
            "passed": matched,
            "fired": fired,
            "conditions": conditions,
        })

    # Final verdict
    if first_match_found:
        winning = next(t for t in trace if t["fired"])
        verdict = winning["rule"]["result"]
        matched_rule = winning["rule"]
    else:
        verdict = "no-match"
        matched_rule = None

    return {
        "verdict": verdict,
        "matched_rule": matched_rule,
        "trace": trace,
    }


def _explain_conditions(rule, body: SimulateRequest, subject) -> list[dict]:
    """Break down each match condition on a rule into matched/not-matched."""
    m = rule.match
    conditions = []

    if not m:
        conditions.append({"label": "match: *", "matched": True, "wildcard": True})
        return conditions

    if "tool" in m:
        ok = m["tool"] == body.tool
        conditions.append({"label": f"tool: {m['tool']}", "matched": ok})
    if "program" in m:
        ok = body.program == m["program"]
        conditions.append({"label": f"program: {m['program']}", "matched": ok})
    if "path" in m:
        import fnmatch
        ok = bool(body.path) and fnmatch.fnmatch(body.path, m["path"])
        conditions.append({"label": f"path: {m['path']}", "matched": ok})
    if "person" in m:
        ok = subject is not None and subject.id == m["person"]
        conditions.append({"label": f"person: {m['person']}", "matched": ok})
    if "group" in m:
        ok = subject is not None and m["group"] in subject.groups
        conditions.append({"label": f"group: {m['group']}", "matched": ok})

    return conditions


# ── Skill endpoints ───────────────────────────────────────────────────────────

def _resolve_skill(email: str) -> str:
    stored = database.get_skill(email)
    if stored:
        return stored
    default_path = Path(__file__).parent / "default_skill.txt"
    return default_path.read_text(encoding="utf-8") if default_path.exists() else ""


@app.get("/skill")
async def get_skill(request: Request):
    email = _require_session(request)
    text = _resolve_skill(email)
    is_custom = database.get_skill(email) is not None
    return {"skill": text, "is_custom": is_custom}


@app.post("/skill")
async def upload_skill(request: Request, file: UploadFile = File(...)):
    email = _require_session(request)
    text = (await file.read()).decode("utf-8")
    database.save_skill(email, text)
    return {"saved": True, "length": len(text)}


@app.get("/skill/download")
async def download_skill(request: Request):
    email = _require_session(request)
    text = _resolve_skill(email)
    return Response(
        content=text,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=skill.txt"},
    )


@app.post("/skill/reset")
async def reset_skill(request: Request):
    email = _require_session(request)
    database.clear_skill(email)
    return {"reset": True}


# ── NL chat ───────────────────────────────────────────────────────────────────

def _engine_for_chat(email: str) -> PolicyEngine:
    return _get_engine(email)

chat_router = create_chat_handler(
    get_engine_fn=_engine_for_chat,
    get_skill_fn=_resolve_skill,
)
app.include_router(chat_router)


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(_server_dir / "static"), html=True), name="static")
