"""
SQLite persistence for Policy Maker.
Tables:
  users(email, rules_yaml, skill_text, agent_token, created_at)
  check_log(id, email, ts, tool, params_json, verdict, rule_id, rule_name)
"""
import sqlite3
import os
import json
from datetime import datetime, timezone
from pathlib import Path

_server_dir = Path(__file__).parent
DB_PATH = Path("/tmp/policy_maker.db") if os.environ.get("VERCEL") else _server_dir / "policy_maker.db"

EMPTY_RULES_YAML = "version: 1\npolicies: []\n"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email         TEXT PRIMARY KEY,
                rules_yaml    TEXT NOT NULL DEFAULT '',
                skill_text    TEXT,
                agent_token   TEXT,
                people_json   TEXT NOT NULL DEFAULT '[]',
                password_hash TEXT,
                created_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS check_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL,
                ts          TEXT NOT NULL,
                tool        TEXT NOT NULL,
                params_json TEXT NOT NULL DEFAULT '{}',
                verdict     TEXT NOT NULL,
                rule_id     TEXT,
                rule_name   TEXT,
                token       TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id          TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                tool        TEXT NOT NULL,
                params_json TEXT NOT NULL DEFAULT '{}',
                rule_id     TEXT,
                rule_name   TEXT,
                subject_id  TEXT,
                created_at  TEXT NOT NULL,
                verdict     TEXT,
                reason      TEXT,
                resolved_at TEXT
            )
        """)
        # Migrations for existing DBs
        for col, definition in [
            ("agent_token", "TEXT"),
            ("people_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("password_hash", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass
        for col, definition in [
            ("token", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE check_log ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def get_user(email: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def create_user(email: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO users (email, rules_yaml, skill_text, created_at) VALUES (?, ?, NULL, ?)",
            (email, EMPTY_RULES_YAML, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_or_create_user(email: str) -> sqlite3.Row:
    user = get_user(email)
    if user is None:
        create_user(email)
        user = get_user(email)
    return user


def save_rules(email: str, yaml_str: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET rules_yaml = ? WHERE email = ?", (yaml_str, email))
        conn.commit()


def get_rules_yaml(email: str) -> str:
    user = get_user(email)
    if user is None:
        return EMPTY_RULES_YAML
    return user["rules_yaml"] or EMPTY_RULES_YAML


def get_skill(email: str) -> str | None:
    user = get_user(email)
    if user is None:
        return None
    return user["skill_text"]


def save_skill(email: str, text: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET skill_text = ? WHERE email = ?", (text, email))
        conn.commit()


def clear_skill(email: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET skill_text = NULL WHERE email = ?", (email,))
        conn.commit()


# ── Agent token ───────────────────────────────────────────────────────────────

def get_agent_token(email: str) -> str | None:
    user = get_user(email)
    return user["agent_token"] if user else None


def save_agent_token(email: str, token: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET agent_token = ? WHERE email = ?", (token, email))
        conn.commit()


def get_people(email: str) -> list:
    """Return list of {name, groups} dicts."""
    user = get_user(email)
    if not user or not user["people_json"]:
        return []
    try:
        return json.loads(user["people_json"])
    except Exception:
        return []


def save_people(email: str, people: list) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET people_json = ? WHERE email = ?",
            (json.dumps(people), email),
        )
        conn.commit()


def delete_user(email: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM check_log WHERE email = ?", (email,))
        conn.execute("DELETE FROM approvals WHERE email = ?", (email,))
        conn.execute("DELETE FROM users WHERE email = ?", (email,))
        conn.commit()


def get_all_users_with_activity() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT u.email, u.created_at, MAX(c.ts) as last_activity
            FROM users u
            LEFT JOIN check_log c ON c.email = u.email
            GROUP BY u.email
            ORDER BY last_activity IS NULL ASC, last_activity DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_email_by_token(token: str) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE agent_token = ?", (token,)
        ).fetchone()
        return row["email"] if row else None


# ── Check log ─────────────────────────────────────────────────────────────────

def log_check(email: str, tool: str, params_json: str, verdict: str,
              rule_id: str | None, rule_name: str | None, token: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO check_log (email, ts, tool, params_json, verdict, rule_id, rule_name, token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, datetime.now(timezone.utc).isoformat(), tool, params_json,
             verdict, rule_id, rule_name, token),
        )
        conn.commit()


def get_all_check_log(limit: int = 500) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM check_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def get_check_log(email: str, limit: int = 50) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            """SELECT * FROM check_log WHERE email = ?
               ORDER BY id DESC LIMIT ?""",
            (email, limit),
        ).fetchall()


def get_password_hash(email: str) -> str | None:
    user = get_user(email)
    return user["password_hash"] if user else None


def set_password_hash(email: str, password_hash: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (password_hash, email))
        conn.commit()


def clear_check_log(email: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM check_log WHERE email = ?", (email,))
        conn.commit()


# ── Approvals ─────────────────────────────────────────────────────────────────

def create_approval(email: str, approval_id: str, tool: str, params_json: str,
                    rule_id: str | None, rule_name: str | None, subject_id: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO approvals
               (id, email, tool, params_json, rule_id, rule_name, subject_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, email, tool, params_json, rule_id, rule_name, subject_id,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_approval(approval_id: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()


def resolve_approval(approval_id: str, verdict: str, reason: str | None) -> sqlite3.Row | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE id = ? AND verdict IS NULL", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE approvals SET verdict = ?, reason = ?, resolved_at = ? WHERE id = ?",
            (verdict, reason, datetime.now(timezone.utc).isoformat(), approval_id),
        )
        conn.commit()
        return conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()


def list_approvals(email: str, pending_only: bool = False) -> list[sqlite3.Row]:
    with _conn() as conn:
        if pending_only:
            return conn.execute(
                "SELECT * FROM approvals WHERE email = ? AND verdict IS NULL ORDER BY created_at DESC",
                (email,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM approvals WHERE email = ? ORDER BY created_at DESC LIMIT 200",
            (email,),
        ).fetchall()


def count_pending_approvals(email: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE email = ? AND verdict IS NULL", (email,)
        ).fetchone()
        return row[0] if row else 0
