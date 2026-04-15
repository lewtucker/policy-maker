"""
Policy Maker Server
FastAPI app with session auth, per-user SQLite storage, and policy evaluation.
"""
import os
import json
import secrets
import tempfile
import uuid
import yaml
import hashlib
import hmac
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File, Header
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
SESSION_SECRET  = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
APP_PASSWORD    = os.environ.get("APP_PASSWORD")
ADMIN_USERNAME  = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "demodemo")
if not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD is not set. Add it to src/server/.env or the environment.")


# ── Password hashing (PBKDF2-HMAC-SHA256, no extra deps) ──────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, dk_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return hmac.compare_digest(dk.hex(), dk_hex)


def _check_login(email: str, password: str) -> bool:
    """Returns True if password matches the user's stored hash, or the APP_PASSWORD fallback."""
    stored = database.get_password_hash(email)
    if stored:
        return _verify_password(password, stored)
    return password == APP_PASSWORD


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
    if not email:
        return RedirectResponse("/login?error=1", status_code=303)
    # Admin check first — admin has no DB row
    if email == ADMIN_USERNAME.lower() and password == ADMIN_PASSWORD:
        request.session["email"] = ADMIN_USERNAME.lower()
        request.session["is_admin"] = True
        return RedirectResponse("/", status_code=303)
    if not _check_login(email, password):
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
    is_admin = request.session.get("is_admin", False)
    if is_admin:
        return {"email": email, "rule_count": 0, "created_at": None, "is_admin": True}
    user = database.get_user(email)
    engine = _get_engine(email)
    return {"email": email, "rule_count": len(engine.rules), "created_at": user["created_at"], "is_admin": False}


# ── Profile endpoints ─────────────────────────────────────────────────────────

class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@app.post("/profile/password")
async def change_password(body: ChangePasswordBody, request: Request):
    email = _require_session(request)
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if not _check_login(email, body.current_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    database.set_password_hash(email, _hash_password(body.new_password))
    return {"changed": True}


@app.delete("/account")
async def delete_account(request: Request):
    email = _require_session(request)
    if request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin account cannot be deleted this way")
    database.delete_user(email)
    request.session.clear()
    return {"deleted": True}


# ── Admin endpoints ───────────────────────────────────────────────────────────

def _require_admin(request: Request) -> str:
    email = _require_session(request)
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return email


@app.get("/admin/users")
async def admin_list_users(request: Request):
    _require_admin(request)
    return {"users": database.get_all_users_with_activity()}


@app.delete("/admin/users/{target_email}")
async def admin_delete_user(target_email: str, request: Request):
    _require_admin(request)
    database.delete_user(target_email)
    return {"deleted": True}


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


_CASE_INSENSITIVE_MATCH_FIELDS = {"tool", "program", "group", "person"}

def _normalize_match(match: dict) -> dict:
    """Lowercase string values in match fields so comparisons are case-insensitive."""
    return {
        k: (v.lower() if isinstance(v, str) and k in _CASE_INSENSITIVE_MATCH_FIELDS else v)
        for k, v in match.items()
    }


def _normalized_rule_dict(body: RuleBody) -> dict:
    d = body.model_dump()
    d["match"] = _normalize_match(d.get("match") or {})
    return d


@app.post("/policies")
async def add_policy(body: RuleBody, request: Request):
    email = _require_session(request)
    engine, tmp = user_engine.get_engine(email)
    try:
        rule = engine.add(_normalized_rule_dict(body))
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
        rule = engine.update(rule_id, _normalized_rule_dict(body))
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
    people = database.get_people(email)
    known_people = [p["name"] for p in people]
    known_groups = list({g for p in people for g in p.get("groups", [])})
    findings = analyze(engine.rules, known_people=known_people, known_groups=known_groups)
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
        params["command"] = f"{body.program.lower()} ..."
    if body.path:
        params["path"] = body.path

    # Build a minimal subject-like object if person/group provided
    subject = None
    if body.person or body.group:
        class _Subject:
            def __init__(self, person_id, groups):
                self.id = person_id
                self.groups = groups
        subject = _Subject(body.person.lower(), [body.group.lower()] if body.group else [])

    # Run evaluation and collect the full trace
    trace = []
    first_match_found = False
    for rule in engine.rules:
        matched = engine._matches(rule, body.tool.lower(), params, subject)
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
    get_people_fn=database.get_people,
)
app.include_router(chat_router)


# ── People & Groups endpoints ─────────────────────────────────────────────────

@app.get("/people")
async def get_people(request: Request):
    email = _require_session(request)
    return {"people": database.get_people(email)}


class PersonBody(BaseModel):
    name: str
    person_id: str = ""   # short ID used in rule person: field (e.g. "lew"); defaults to first word of name
    groups: list[str] = []
    telegram_id: str = ""


@app.post("/people")
async def save_people(body: list[PersonBody], request: Request):
    email = _require_session(request)
    people = [p.model_dump() for p in body]
    database.save_people(email, people)
    return {"saved": len(people)}


# ── Agent token endpoints ─────────────────────────────────────────────────────

@app.get("/token")
async def get_token(request: Request):
    email = _require_session(request)
    token = database.get_agent_token(email)
    return {"token": token}


@app.post("/token/generate")
async def generate_token(request: Request):
    email = _require_session(request)
    token = secrets.token_hex(32)
    database.save_agent_token(email, token)
    return {"token": token}


class TokenBody(BaseModel):
    token: str


@app.post("/token/set")
async def set_token(body: TokenBody, request: Request):
    email = _require_session(request)
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    existing = database.get_email_by_token(token)
    if existing and existing != email:
        raise HTTPException(status_code=409, detail="Token is already in use by another account")
    database.save_agent_token(email, token)
    return {"token": token}


@app.delete("/token")
async def revoke_token(request: Request):
    email = _require_session(request)
    database.save_agent_token(email, None)
    return {"revoked": True}


# ── Activity log endpoint ─────────────────────────────────────────────────────

@app.get("/activity")
async def get_activity(request: Request, limit: int = 50):
    email = _require_session(request)
    rows = database.get_check_log(email, limit=min(limit, 200))
    return {"activity": [dict(r) for r in rows]}


@app.delete("/activity")
async def clear_activity(request: Request):
    email = _require_session(request)
    database.clear_check_log(email)
    return {"cleared": True}


# ── OpenClaw /check endpoint ──────────────────────────────────────────────────

def _resolve_caller(caller_id: str, people: list) -> dict | None:
    """
    Match caller_id against the people roster using multiple strategies, in order:
    1. Exact telegram_id match      e.g. "tg:6741893378"
    2. person_id match              e.g. "lew"
    3. Full name match              e.g. "Lew Tucker"
    4. First word of name match     e.g. "Lew"
    All name/id comparisons are case-insensitive.
    """
    needle = caller_id.strip().lower()

    # 1. Telegram ID (exact)
    for p in people:
        if p.get("telegram_id", "").lower() == needle:
            return p

    # 2. Person ID
    for p in people:
        if p.get("person_id", "").strip().lower() == needle:
            return p

    # 3. Full name
    for p in people:
        if p.get("name", "").strip().lower() == needle:
            return p

    # 4. First word of name
    for p in people:
        first = p.get("name", "").strip().lower().split()[0] if p.get("name") else ""
        if first and first == needle:
            return p

    return None

class CheckRequest(BaseModel):
    tool: str
    params: dict = {}
    person: str = ""
    group: str = ""
    channel_id: str = ""   # e.g. "tg:6741893378" — resolved to a person via telegram_id


@app.post("/check")
async def check(body: CheckRequest, authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization[len("Bearer "):]
    email = database.get_email_by_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid agent token")

    engine = _get_engine(email)

    # Build params matching policy_engine expectations
    params = {}
    command = body.params.get("command", "").lower()
    if command:
        params["command"] = command
    path = body.params.get("path", "")
    if path:
        params["path"] = path

    # Resolve caller identity — check channel_id, then fall back to params._caller
    caller_id = body.channel_id or body.params.get("_caller", "")
    person_name = body.person
    person_id_str = body.person  # short ID used in rule matching
    person_groups = [body.group] if body.group else []
    if caller_id:
        people = database.get_people(email)
        matched_person = _resolve_caller(caller_id, people)
        if matched_person:
            person_name = matched_person.get("name", person_name)
            person_groups = matched_person.get("groups", person_groups)
            # Use person_id if set, otherwise fall back to first word of name
            raw_id = matched_person.get("person_id", "").strip()
            if not raw_id:
                raw_id = person_name.split()[0] if person_name else person_name
            person_id_str = raw_id

    # Build subject if person/group resolved
    subject = None
    if person_id_str or person_groups:
        class _Subject:
            def __init__(self, pid, groups):
                self.id = pid
                self.groups = groups
        subject = _Subject(person_id_str.lower(), [g.lower() for g in person_groups])

    # Find first matching rule
    verdict = "no-match"
    rule_id = None
    rule_name = None
    for rule in engine.rules:
        if engine._matches(rule, body.tool.lower(), params, subject):
            verdict = rule.result
            rule_id = rule.id
            rule_name = rule.name
            break

    # Log the call (include resolved identity in params for display)
    log_params = dict(body.params)
    if caller_id:
        log_params["_caller"] = person_id_str or caller_id
    database.log_check(
        email=email,
        tool=body.tool,
        params_json=json.dumps(log_params),
        verdict=verdict,
        rule_id=rule_id,
        rule_name=rule_name,
    )

    # For pending verdicts, create an approval record the admin can resolve
    approval_id = None
    if verdict == "pending":
        approval_id = str(uuid.uuid4())
        database.create_approval(
            email=email,
            approval_id=approval_id,
            tool=body.tool,
            params_json=json.dumps(log_params),
            rule_id=rule_id,
            rule_name=rule_name,
            subject_id=person_id_str.lower() if person_id_str else None,
        )

    if verdict == "no-match":
        return {"verdict": "deny", "reason": "No policy matched — failing closed"}
    if verdict == "allow":
        return {"verdict": "allow", "reason": f"Allowed by '{rule_name or rule_id}'"}
    if verdict == "deny":
        return {"verdict": "deny", "reason": f"Denied by '{rule_name or rule_id}'"}
    if verdict == "pending":
        return {
            "verdict": "pending",
            "reason": f"Pending approval — '{rule_name or rule_id}'",
            "approval_id": approval_id,
        }

    return {"verdict": "deny", "reason": "Unknown verdict"}


# ── Approval endpoints ────────────────────────────────────────────────────────

@app.get("/approvals")
async def list_approvals(request: Request, pending_only: bool = False):
    email = _require_session(request)
    rows = database.list_approvals(email, pending_only=pending_only)
    return {"approvals": [dict(r) for r in rows]}


@app.get("/approvals/count")
async def approval_count(request: Request):
    email = _require_session(request)
    return {"pending": database.count_pending_approvals(email)}


@app.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request,
                       authorization: str = Header(default="")):
    """Accessible by session (admin) or Bearer token (agent polling for resolution)."""
    email = request.session.get("email")
    if not email and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        email = database.get_email_by_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = database.get_approval(approval_id)
    if row is None or row["email"] != email:
        raise HTTPException(status_code=404, detail="Approval not found")
    return dict(row)


class ResolveBody(BaseModel):
    verdict: str          # "allow" | "deny"
    reason: str = ""


@app.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: str, body: ResolveBody, request: Request):
    email = _require_session(request)
    if body.verdict not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="verdict must be 'allow' or 'deny'")
    row = database.get_approval(approval_id)
    if row is None or row["email"] != email:
        raise HTTPException(status_code=404, detail="Approval not found")
    updated = database.resolve_approval(approval_id, body.verdict, body.reason or None)
    if updated is None:
        raise HTTPException(status_code=409, detail="Already resolved")
    return dict(updated)


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(_server_dir / "static"), html=True), name="static")
