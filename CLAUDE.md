# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Policy Maker** is a web-based sandbox for designing, testing, and enforcing AI agent governance policies. Users log in with an email address, build policy rules via a Claude-powered assistant, simulate how rules evaluate against hypothetical actions, and analyze their rule set for health issues. Policies are stored per-user in SQLite.

It also acts as a live policy server: AI agents (OpenClaw / nanoclaw) call the `/check` endpoint with a Bearer token before each tool use, and receive an allow / deny / pending verdict in real time. Pending verdicts create approval records that admins review in the Approvals tab.

GitHub: https://github.com/lewtucker/policy-maker (private)  
Production: https://policy.lewtucker.net

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
  server.py           FastAPI app — auth, policy CRUD, simulate, check, approval endpoints
  database.py         SQLite wrapper (users, check_log, approvals tables)
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
.github/workflows/
  deploy.yml          CI/CD: rsync + systemctl restart on push to src/ (docs-only pushes skip deploy)
```

## Architecture

**Auth**: `SessionMiddleware` with a session cookie (`pm_session`). Any email accepted; password is always `"ZPR"`. New email → creates a user row in SQLite.

**Per-user storage**: `database.py` wraps a single SQLite file (`policy_maker.db`). Each user row stores `rules_yaml`, `skill_text`, `agent_token`, and `people_json`. Related tables: `check_log` (activity), `approvals` (pending verdicts).

**Policy engine bridge**: `PolicyEngine` expects a file path. `user_engine.py` writes the user's YAML to a `tempfile`, passes the path to `PolicyEngine`, then reads the (mutated) file back after any CRUD operation and saves it to SQLite.

**NL chat** (`POST /chat`): Loads the user's engine + skill prompt, runs the analyzer to populate `{analysis_json}`, substitutes both into the system prompt, and calls Claude. Extracts `PROPOSED_RULE` JSON blocks from the response for the UI to render as accept/discard cards.

**Test Action** (`POST /simulate`): Runs `engine._matches()` against every rule in priority order and returns the full trace — each rule's per-condition breakdown (matched/missed/wildcard) plus which rule fired. Keeps last 5 results in the UI.

**Live agent check** (`POST /check`): Bearer token → user lookup → caller identity resolution → rule evaluation → response + activity log entry. If verdict is `pending`, creates an approval record and returns `approval_id`. Caller identity is resolved via `_resolve_caller()` using four strategies in order: Telegram ID, person_id, full name, first word of name (all case-insensitive).

**Approvals**: Pending verdicts from `/check` are stored in the `approvals` table. Admins resolve them via the Approvals tab (`POST /approvals/{id}`). Agents can poll `GET /approvals/{id}` with their Bearer token to check if a decision has been made.

**Multi-user**: Each user's Bearer token both authenticates and routes requests to their own rule set, people roster, activity log, and approval queue. Multiple agents can share one server independently.

## UI Navigation (left sidebar)

1. **Home** — welcome + how-to-use instructions
2. **Create Rules** — Claude chat assistant + skill prompt sidebar; `⬡ Policy Analyst` button sends a health-evaluation prompt; after a rule is accepted the chat resets
3. **Test Action** — Mad Libs form (`[person] in group [group] calls [tool] [program] on path [path]`); shows verdict + full evaluation trace; keeps last 5 results; Reset button
4. **Policy Rules** — rule table with edit/delete, health analyzer panel, import/export YAML, People & Groups roster
5. **Approvals** — pending approval cards with Approve/Deny buttons; resolved cards shown below; badge count in nav polls every 30s; tab auto-polls every 8s
6. **Activity** — live feed of `/check` evaluations; auto-polls every 10s with flash-on-new and auto-scroll; Clear button; expandable chevron rows

## Key API Endpoints

| Method | Path | Auth | Notes |
| ------ | ---- | ---- | ----- |
| POST | `/login` | — | Form: email + password |
| POST | `/logout` | session | Clears session |
| GET | `/me` | session | Returns email + rule_count |
| GET/POST | `/policies` | session | List / add rule |
| PUT/DELETE | `/policies/{id}` | session | Edit / delete (403 if protected) |
| POST | `/policies/delete-all` | session | Skips protected rules |
| GET | `/policies/analyze` | session | Tier 1+2 health findings |
| POST | `/policies/import` | session | Merge YAML into user's rule set |
| GET | `/policies/export` | session | Download as policies.yaml |
| POST | `/simulate` | session | Returns verdict + full trace |
| GET/POST | `/skill` | session | Get or upload skill prompt |
| GET | `/skill/download` | session | Download as skill.txt |
| POST | `/skill/reset` | session | Revert to default_skill.txt |
| POST | `/chat` | session | NL assistant (Claude) |
| GET/POST | `/people` | session | Get or save people roster |
| GET/POST/DELETE | `/token` | session | Agent token management |
| POST | `/token/set` | session | Save custom token string |
| POST | `/token/generate` | session | Generate random token |
| GET | `/activity` | session | Check log (limit param) |
| DELETE | `/activity` | session | Clear activity log |
| GET | `/approvals` | session | List approvals (pending_only param) |
| GET | `/approvals/count` | session | Pending count for badge |
| GET | `/approvals/{id}` | session or Bearer | Get single approval (agent polling) |
| POST | `/approvals/{id}` | session | Resolve: `{verdict, reason}` |
| POST | `/check` | Bearer | Live agent policy check |

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
  path: "/project/**"
  person: lew           # matches person_id from the People roster
  group: engineering
```

**Match field behaviour:**
- `path` and `program` support `*`/`**` globs via Python `fnmatch`. Note: `*` matches `/` so `/foo/*.txt` also matches `/foo/sub/file.txt`.
- `tool`, `person`, `group` are exact string matches — no wildcards, no multiple values.
- All match fields are case-normalized to lowercase on save and at evaluation time.
- To match multiple tools/programs, write one rule per value.

## People Roster & Identity Resolution

Each person entry has: `name` (display), `person_id` (short ID used in rules), `groups` (list), `telegram_id` (e.g. `tg:6741893378`).

`_resolve_caller(caller_id, people)` in `server.py` tries four strategies in order:
1. Exact `telegram_id` match
2. `person_id` match (case-insensitive)
3. Full `name` match (case-insensitive)
4. First word of `name` (case-insensitive)

## Things NOT to change

- `policy_engine.py` and `policy_analyzer.py` are copied verbatim from `../OC_Policy` — do not modify them. If the engine needs adaptation, do it in `user_engine.py` or `server.py`.
- The YAML structure of rules is intentionally identical to OC_Policy so rule sets are portable between the two systems.
