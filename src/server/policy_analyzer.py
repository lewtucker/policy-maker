"""
Policy Analyzer — Phase D Tiers 1 & 2
Inspects the full rule set for shadows, conflicts, orphan identity references,
gaps, unused rules, overly-broad allows, and uncovered groups.

Tier 1 checks (deterministic, run on every policy write):
  - shadow:    a higher-priority rule makes a lower rule unreachable
  - conflict:  two rules at equal priority match the same conditions but differ in result
  - orphan:    a rule references a person or group not found in identities
  - gap:       a catch-all match={} rule exists, meaning some actions fall through silently

Tier 2 checks (heuristic, require audit history):
  - unused:    a rule has never matched anything in the audit log
  - broad:     an allow rule has 0 or 1 match conditions (very permissive)
  - uncovered: a known group has no rules targeting it
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from policy_engine import Rule

Severity = Literal["error", "warning", "info"]


@dataclass
class Finding:
    severity: Severity
    check: str           # "shadow" | "conflict" | "orphan" | "gap" | "unused" | "broad" | "uncovered"
    rule_id: str         # primary rule involved
    related_id: str | None  # second rule (for shadow/conflict)
    message: str

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity,
            "check": self.check,
            "rule_id": self.rule_id,
            "message": self.message,
        }
        if self.related_id:
            d["related_id"] = self.related_id
        return d


def _match_conditions_subset(high: dict, low: dict) -> bool:
    """
    Return True if `high` match conditions are a subset of (or equal to) `low`.
    If every condition in `high` is also present and equal in `low`, then any
    request that matches `high` will also match `low` — meaning `low` is
    shadowed by `high` for that intersection.

    Conversely, if `high` has FEWER or EQUAL conditions than `low`, it is
    broader and will fire first for everything `low` would match.
    """
    # Empty match in high = matches everything → shadows anything below it
    if not high:
        return True

    # Every condition in `high` must also appear in `low` (or low must be broader)
    for key, val in high.items():
        if key not in low:
            # high restricts something low doesn't — low is actually broader in this dimension,
            # so high doesn't fully shadow low (low matches more things)
            return False
        if low[key] != val:
            return False

    return True


def _matches_overlap(a: dict, b: dict) -> bool:
    """
    Return True if two match condition dicts could ever match the same request.
    Two rules overlap if neither has a condition that directly contradicts the other.
    """
    for key in set(a) | set(b):
        if key in a and key in b and a[key] != b[key]:
            return False  # direct contradiction — they can never both match
    return True


def analyze(
    rules: list[Rule],
    known_people: list[str],
    known_groups: list[str],
    audit_entries: list | None = None,
) -> list[Finding]:
    """
    Run Tier 1 (deterministic) and optionally Tier 2 (heuristic) checks.
    Rules must be sorted by priority descending (as PolicyEngine maintains them).
    Pass audit_entries to enable Tier 2 checks.
    """
    findings: list[Finding] = []

    # ── Shadow detection ──────────────────────────────────────────────────────
    # For each pair (high, low) where high.priority > low.priority:
    # if high's match is a subset of low's match conditions, high shadows low.
    for i, high in enumerate(rules):
        for low in rules[i + 1:]:
            if high.priority <= low.priority:
                continue
            if _match_conditions_subset(high.match, low.match):
                findings.append(Finding(
                    severity="warning",
                    check="shadow",
                    rule_id=high.id,
                    related_id=low.id,
                    message=(
                        f"Rule '{high.id}' (priority {high.priority}, result={high.result}) "
                        f"shadows '{low.id}' (priority {low.priority}, result={low.result}). "
                        f"'{low.id}' may never be reached."
                    ),
                ))

    # ── Conflict detection ─────────────────────────────────────────────────────
    # Two rules at the same priority that overlap in match conditions but differ in result.
    # First-match-wins makes the outcome depend on insertion order — fragile.
    seen_pairs: set[frozenset[str]] = set()
    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            pair = frozenset({a.id, b.id})
            if pair in seen_pairs:
                continue
            if a.priority == b.priority and a.result != b.result and _matches_overlap(a.match, b.match):
                seen_pairs.add(pair)
                findings.append(Finding(
                    severity="warning",
                    check="conflict",
                    rule_id=a.id,
                    related_id=b.id,
                    message=(
                        f"Rules '{a.id}' and '{b.id}' share priority {a.priority} "
                        f"and have overlapping match conditions but different results "
                        f"({a.result} vs {b.result}). Outcome depends on insertion order."
                    ),
                ))

    # ── Orphan identity references ─────────────────────────────────────────────
    people_set = set(known_people)
    groups_set = set(known_groups)

    for rule in rules:
        if "person" in rule.match and rule.match["person"] not in people_set:
            findings.append(Finding(
                severity="warning",
                check="orphan",
                rule_id=rule.id,
                related_id=None,
                message=(
                    f"Rule '{rule.id}' references person '{rule.match['person']}' "
                    f"which is not in identities. This rule will never match."
                ),
            ))
        if "group" in rule.match and rule.match["group"] not in groups_set:
            findings.append(Finding(
                severity="warning",
                check="orphan",
                rule_id=rule.id,
                related_id=None,
                message=(
                    f"Rule '{rule.id}' references group '{rule.match['group']}' "
                    f"which is not in identities. This rule will never match."
                ),
            ))

    # ── Gap detection ─────────────────────────────────────────────────────────
    # If the highest-priority catch-all (match={}) is "allow" or "pending",
    # unknown actions are permitted or queued rather than denied — flag it.
    catch_alls = [r for r in rules if not r.match]
    if catch_alls:
        top_catch_all = catch_alls[0]  # highest priority since rules are sorted desc
        if top_catch_all.result == "allow":
            findings.append(Finding(
                severity="warning",
                check="gap",
                rule_id=top_catch_all.id,
                related_id=None,
                message=(
                    f"Rule '{top_catch_all.id}' is a catch-all (match={{}}) with result=allow. "
                    f"Any action not matched by a higher-priority rule will be permitted. "
                    f"Consider using result=deny or result=pending as the fallback."
                ),
            ))
        elif top_catch_all.result == "pending":
            findings.append(Finding(
                severity="info",
                check="gap",
                rule_id=top_catch_all.id,
                related_id=None,
                message=(
                    f"Rule '{top_catch_all.id}' is a catch-all (match={{}}) with result=pending. "
                    f"Unmatched actions will queue for approval rather than being denied outright."
                ),
            ))

    # ── Tier 2: Broad allow rules ──────────────────────────────────────────────
    # Any allow rule with 0 or 1 match conditions is very permissive.
    # (0-condition catch-alls are already covered by gap check above.)
    for rule in rules:
        if rule.result == "allow" and len(rule.match) <= 1 and not rule.match:
            # Already flagged by gap check — skip
            pass
        elif rule.result == "allow" and len(rule.match) == 1:
            key, val = next(iter(rule.match.items()))
            findings.append(Finding(
                severity="info",
                check="broad",
                rule_id=rule.id,
                related_id=None,
                message=(
                    f"Rule '{rule.id}' allows everything matching only {key}={val!r}. "
                    f"Consider adding more specific conditions to limit scope."
                ),
            ))

    # ── Tier 2: Uncovered groups ───────────────────────────────────────────────
    # Groups that exist in identities but are not referenced in any rule.
    groups_in_rules = {r.match["group"] for r in rules if "group" in r.match}
    for group in known_groups:
        if group not in groups_in_rules:
            findings.append(Finding(
                severity="info",
                check="uncovered",
                rule_id="—",
                related_id=None,
                message=(
                    f"Group '{group}' has no rules targeting it. "
                    f"Members will only match generic (non-group-scoped) rules."
                ),
            ))

    # ── Tier 2: Unused rules (requires audit) ─────────────────────────────────
    if audit_entries:
        matched_rule_ids = {e.rule_id for e in audit_entries if e.rule_id}
        for rule in rules:
            if rule.id not in matched_rule_ids:
                findings.append(Finding(
                    severity="info",
                    check="unused",
                    rule_id=rule.id,
                    related_id=None,
                    message=(
                        f"Rule '{rule.id}' has never matched any request in the audit log. "
                        f"It may be redundant, misconfigured, or simply not yet exercised."
                    ),
                ))

    return findings


def summarize(findings: list[Finding]) -> dict:
    """Return a counts summary for the health panel."""
    return {
        "total": len(findings),
        "errors":   sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "info":     sum(1 for f in findings if f.severity == "info"),
    }
