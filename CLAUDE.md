# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Policy-maker** is a standalone policy exploration and analysis tool — a variant of the OC_Policy system at `../OC_Policy` stripped of OpenClaw integration. Users log in with an email address, explore and build policy rules, and analyze them using a Claude-powered assistant. Policies are stored per-user in a database.

The reference implementation to draw from is `../OC_Policy/src/server/`. Policy-maker reuses the policy engine, policy analyzer, and NL policy authoring logic but removes the enforcement plugin, approval queue, and Telegram integration.

## Planned Architecture

### Authentication
- Email + fixed password `"ZPR"` (no real auth — just user partitioning)
- New email → new user record with an empty rule set
- Existing email → retrieve saved rule set from database

### Backend (Python/FastAPI — adapt from OC_Policy)
Key modules to carry over from `../OC_Policy/src/server/`:
- `policy_engine.py` — Rule parser and priority-based evaluator (keep as-is)
- `policy_analyzer.py` — Tier 1+2 policy health checks (keep as-is)
- `nl_policy.py` — Claude-powered natural language rule authoring (adapt)

Modules to drop or replace:
- `approvals.py` — Not needed (no OpenClaw enforcement)
- `audit.py` — Not needed (no live enforcement)
- `identity.py` — Replace with simple email-based user store
- Token auth (agent/admin split) → replace with session auth tied to email login

### Database
Policies per user stored in a database keyed by email. Each user record holds:
- Their rule set (same YAML structure as `policies.yaml` in OC_Policy)
- The agent skill prompt (uploaded or default)

### UI (adapt from OC_Policy's `src/server/static/index.html`)
Two tabs:
1. **Policies tab** — View, add, edit, delete rules. Same UI elements as the OC_Policy Manager Policies page.
2. **Rule-maker tab** — NL policy assistant chat panel + upload/download the agent skill prompt file.

Drop from OC_Policy UI: Dashboard, Approvals tab, Identities tab, Activity feed.

## Key Design Decisions from the Spec

- Any email is accepted; password is always `"ZPR"`
- Agent skill prompt is stored per-user in the database; users can upload a modified version
- No OpenClaw connection, no enforcement — this is an explorer/sandbox tool only
- Policy rule structure remains identical to OC_Policy (YAML schema, priority, conditions, effects)

## Reference Files in OC_Policy

When building policy-maker, these are the most relevant files to read:

| File | Purpose |
|------|---------|
| `../OC_Policy/src/server/policy_engine.py` | Core rule evaluation logic |
| `../OC_Policy/src/server/policy_analyzer.py` | Tier 1+2 analysis checks |
| `../OC_Policy/src/server/nl_policy.py` | Claude-powered NL authoring |
| `../OC_Policy/src/server/server.py` | FastAPI app structure to adapt |
| `../OC_Policy/src/server/static/index.html` | UI to adapt (Policies tab) |
| `../OC_Policy/src/server/requirements.txt` | Python dependencies baseline |
| `../OC_Policy/src/server/test_policy_suite.py` | Test patterns to follow |

## Policy Rule Schema

Rules are attribute-based and evaluated highest-priority-first (first match wins):

```yaml
- id: unique-rule-id
  name: Human-readable name
  description: What this rule does
  result: allow | deny | pending
  priority: 100          # Higher = evaluated first
  protected: false       # If true, cannot be edited/deleted via API
  match:
    tool: bash           # Optional: tool name to match
    program: git         # Optional: program name
    path: "/project/**"  # Optional: glob path match
    person: alice        # Optional: person identity
    group: engineering   # Optional: group membership
```
