# Policy Maker

Policy Maker is a sandbox tool for designing, testing, and managing governance policies for AI agents. It gives you a web UI to build rules in plain English, simulate how those rules would evaluate against hypothetical agent actions, and connect a live AI agent so its requests are gated by your policy.

It is designed to work alongside [OpenClaw](https://github.com/lewtucker/OC_Policy) / nanoclaw — Claude-based AI coding agents — but the rule authoring and simulation features work entirely on their own.

## What it does

- **Author rules in plain English** — a Claude-powered assistant turns natural language ("block curl", "allow git for the engineering team") into structured YAML policy rules
- **Simulate actions** — test any hypothetical tool call against your rules and see a full evaluation trace showing which rule matched and why
- **Analyze your policy** — automatic health checks catch shadows, conflicts, overly-broad allows, unreachable rules, and gaps in coverage
- **Gate live agent requests** — expose a `/check` endpoint that your AI agent calls before every tool use; the policy engine returns allow / deny / pending in real time
- **Track activity** — a live feed shows every evaluation your agent has made, with the full request and matched rule visible on demand

## Architecture

```
Browser (Single-page app)
    │
    │  HTTPS
    ▼
FastAPI server  (src/server/server.py)
    │
    ├── Session auth       any email + shared password → per-user session cookie
    ├── SQLite             one row per user: rules_yaml, skill_text, agent_token, people_json
    ├── PolicyEngine       pure YAML rule evaluator (policy_engine.py)
    ├── PolicyAnalyzer     static + heuristic health checks (policy_analyzer.py)
    ├── NL chat            Claude claude-sonnet-4-6 via Anthropic API (nl_policy.py)
    └── /check             Bearer-token endpoint for live agent integration
```

### Rule evaluation

Rules are stored as YAML, evaluated highest-priority-first. The first rule whose conditions all match wins; its `result` field (`allow`, `deny`, or `pending`) is returned. An empty `match:` block is a catch-all. All matching is case-insensitive.

```yaml
version: 1
policies:
  - id: allow-git
    name: Allow git for everyone
    result: allow
    priority: 100
    match:
      tool: bash
      program: git

  - id: deny-all
    name: Default deny
    result: deny
    priority: 1
    match: {}
```

Match conditions are AND-ed. Every condition present in a rule must be satisfied for that rule to fire:

| Field | What it matches |
|-------|----------------|
| `tool` | Tool name (`bash`, `read`, `write`, `web_fetch`, …) |
| `program` | First word of the shell command (`git`, `npm`, `rm`, …) — bash only |
| `path` | File path glob (`/project/**`, `*.env`) |
| `person` | Short person ID from the People roster |
| `group` | Group name from the People roster |

### Identity resolution

The People & Groups roster maps display names and Telegram IDs to short person IDs. When an agent request arrives at `/check`, the caller's Telegram ID (`tg:123456789`) is looked up in the roster, and the matching person's **Person ID** (e.g. `lew`) is passed as the subject. Rules then match against that short ID in their `person:` field.

### NL assistant

The chat assistant (`/chat`) receives the user's current rule set and a live analysis report as context, then calls Claude. When Claude proposes a rule it embeds a `PROPOSED_RULE` JSON block in its reply; the UI renders this as an accept/discard card. Accepted rules are added to the user's policy set immediately.

### Live agent integration

The `/check` endpoint accepts a JSON POST with a Bearer token:

```
POST /check
Authorization: Bearer <agent-token>

{
  "tool": "bash",
  "params": { "command": "git push origin main", "_caller": "tg:6741893378" }
}
```

The server resolves the caller's identity, evaluates the request against their rule set, logs the result, and returns:

```json
{ "verdict": "allow", "reason": "Allowed by 'Allow git for everyone'" }
```

## Tech stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn, PyYAML, Anthropic Python SDK
- **Frontend**: Vanilla JS single-page app, no build step
- **Storage**: SQLite (one file, one row per user)
- **Auth**: Starlette `SessionMiddleware` with a shared password
- **AI**: Claude claude-sonnet-4-6 for rule authoring assistance
- **Deployment**: Systemd service on a Linux VPS; GitHub Actions CI/CD on push to main
