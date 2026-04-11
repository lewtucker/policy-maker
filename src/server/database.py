"""
SQLite persistence for Policy Maker.
Tables:
  users(email, rules_yaml, skill_text, agent_token, created_at)
  check_log(id, email, ts, tool, params_json, verdict, rule_id, rule_name)
"""
import sqlite3
import os
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
                email        TEXT PRIMARY KEY,
                rules_yaml   TEXT NOT NULL DEFAULT '',
                skill_text   TEXT,
                agent_token  TEXT,
                created_at   TEXT NOT NULL
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
                rule_name   TEXT
            )
        """)
        # Migration: add agent_token column if upgrading an existing DB
        try:
            conn.execute("ALTER TABLE users ADD COLUMN agent_token TEXT")
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


def get_email_by_token(token: str) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE agent_token = ?", (token,)
        ).fetchone()
        return row["email"] if row else None


# ── Check log ─────────────────────────────────────────────────────────────────

def log_check(email: str, tool: str, params_json: str, verdict: str,
              rule_id: str | None, rule_name: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO check_log (email, ts, tool, params_json, verdict, rule_id, rule_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email, datetime.now(timezone.utc).isoformat(), tool, params_json,
             verdict, rule_id, rule_name),
        )
        conn.commit()


def get_check_log(email: str, limit: int = 50) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            """SELECT * FROM check_log WHERE email = ?
               ORDER BY id DESC LIMIT ?""",
            (email, limit),
        ).fetchall()
