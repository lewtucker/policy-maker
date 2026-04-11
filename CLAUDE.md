# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Policy Maker** is a standalone sandbox for designing and testing AI agent governance policies. Users log in with an email address, build policy rules via a Claude-powered assistant, simulate how rules evaluate against hypothetical actions, and analyze their rule set for health issues. Policies are stored per-user in SQLite. There is no live agent connection — this is an exploration tool only.

GitHub: https://github.com/lewtucker/policy-maker (private)

## Running Locally

```bash
cd src/server
./start.sh          # installs deps, starts on http://localhost:8080
# password: ZPR (any email accepted)
```

Or directly:
```bash
cd src/server
uvicorn server:app --reload --port 8080
```

## File Structure

```
src/server/
  server.py           FastAPI app — auth, policy CRUD, simulate, skill endpoints
  database.py         SQLite wrapper (users table: email, rules_yaml, skill_text)
  user_engine.py      Bridge between per-user YAML in DB and PolicyEngine (temp file pattern)
  policy_engine.py    Rule parser + evaluator — copied verbatim from OC_Policy, do not modify
  policy_analyzer.py  Tier 1+2 health checks — copied verbatim from OC_Policy, do not modify
  nl_policy.py        Claude-powered chat endpoint; injects live rules + analyzer findings
  default_skill.txt   Default system prompt for the NL assistant
  requirements.txt
  start.sh
  static/
    index.html        Single-page app (all JS inline)
    login.html        Email + password login form
```

## Architecture

**Auth**: `SessionMiddleware` with a session cookie (`pm_session`). Any email accepted; password is always `"ZPR"`. New email → creates a user row in SQLite.

**Per-user storage**: `database.py` wraps a single SQLite file (`policy_maker.db`, in `/tmp/` on Vercel). Each user row stores `rules_yaml` (YAML string) and `skill_text` (NL assistant system prompt, NULL = use default).

**Policy engine bridge**: `PolicyEngine` expects a file path. `user_engine.py` writes the user's YAML to a `tempfile`, passes the path to `PolicyEngine`, then reads the (mutated) file back after any CRUD operation and saves it to SQLite.

**NL chat** (`POST /chat`): Loads the user's engine + skill prompt, runs the analyzer to populate `{analysis_json}`, substitutes both into the system prompt, and calls Claude. Extracts `PROPOSED_RULE` JSON blocks from the response for the UI to render as accept/discard cards.

**Simulate** (`POST /simulate`): Runs `engine._matches()` against every rule in priority order and returns the full trace — each rule's per-condition breakdown (matched/missed/wildcard) plus which rule fired.

## UI Navigation (left sidebar)

1. **Home** — welcome + how-to-use instructions, clickable cards that navigate to each tab
2. **Create Rules** — Claude chat assistant (`AI Agent Rule Maker and Analysis` header) + skill prompt sidebar; `⬡ Policy Analyst` button sends a canned health-evaluation prompt; after a rule is accepted the chat resets to the welcome message
3. **Simulate** — Mad Libs sentence form (`[person] in group [group] calls [tool] [program] on path [path]`) with inline auto-sizing inputs; quick presets; result shows verdict block + full evaluation trace
4. **Policies** — rule table with edit/delete, health analyzer panel, import YAML, export YAML, delete all

## Key API Endpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/login` | Form: email + password |
| POST | `/logout` | Clears session |
| GET | `/me` | Returns email + rule_count |
| GET/POST | `/policies` | List / add rule |
| PUT/DELETE | `/policies/{id}` | Edit / delete (403 if protected) |
| POST | `/policies/delete-all` | Skips protected rules |
| GET | `/policies/analyze` | Tier 1+2 health findings |
| POST | `/policies/import` | Merge YAML into user's rule set |
| GET | `/policies/export` | Download as policies.yaml |
| POST | `/simulate` | Returns verdict + full trace |
| GET/POST | `/skill` | Get or upload skill prompt |
| GET | `/skill/download` | Download as skill.txt |
| POST | `/skill/reset` | Revert to default_skill.txt |
| POST | `/chat` | NL assistant (Claude) |

## Policy Rule Schema

Rules are evaluated highest-priority-first; first match wins. Empty `match` = catch-all.

```yaml
id: unique-kebab-id
name: Human-readable name
description: What this rule does
result: allow | deny | pending
priority: 100
protected: false        # true = cannot be edited/deleted via API
match:                  # all fields optional, ANDed together
  tool: bash
  program: git
  path: "/project/**"  # supports * and ** globs
  person: alice
  group: engineering
```

## Things NOT to change

- `policy_engine.py` and `policy_analyzer.py` are copied verbatim from `../OC_Policy` — do not modify them. If the engine needs adaptation, do it in `user_engine.py`.
- The YAML structure of rules is intentionally identical to OC_Policy so rule sets are portable between the two systems.
