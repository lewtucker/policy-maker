"""
SQLite persistence for Policy Maker.
One table: users(email, rules_yaml, skill_text, created_at)
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
                email       TEXT PRIMARY KEY,
                rules_yaml  TEXT NOT NULL DEFAULT '',
                skill_text  TEXT,
                created_at  TEXT NOT NULL
            )
        """)
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
