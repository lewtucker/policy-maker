"""
Bridge between per-user YAML stored in SQLite and the PolicyEngine class.
PolicyEngine expects a file path, so we write to a temp file, operate, read back.
"""
import tempfile
import yaml
from pathlib import Path

import database
from policy_engine import PolicyEngine


def get_engine(email: str) -> tuple[PolicyEngine, Path]:
    """
    Load the user's rules YAML from the DB, write to a temp file,
    and return (engine, temp_path). Caller must delete temp_path when done,
    or use as a context manager via engine_for_user().
    """
    yaml_str = database.get_rules_yaml(email)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    tmp.write(yaml_str)
    tmp.flush()
    tmp.close()
    engine = PolicyEngine(Path(tmp.name))
    return engine, Path(tmp.name)


def save_engine(email: str, tmp_path: Path) -> None:
    """Read the (possibly mutated) temp file and persist back to the DB."""
    yaml_str = tmp_path.read_text(encoding="utf-8")
    database.save_rules(email, yaml_str)


def import_yaml(email: str, yaml_str: str) -> int:
    """
    Merge rules from yaml_str into the user's existing rule set.
    Rules with duplicate IDs overwrite existing ones.
    Returns the count of rules imported.
    """
    try:
        incoming = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")

    new_rules = incoming.get("policies", [])
    if not new_rules:
        return 0

    existing_yaml = database.get_rules_yaml(email)
    existing = yaml.safe_load(existing_yaml) or {}
    existing_rules = existing.get("policies", [])

    # Index existing by id for quick lookup
    by_id = {r["id"]: r for r in existing_rules}
    for rule in new_rules:
        by_id[rule["id"]] = rule

    merged = {"version": 1, "policies": list(by_id.values())}
    database.save_rules(email, yaml.dump(merged, sort_keys=False, allow_unicode=True))
    return len(new_rules)
