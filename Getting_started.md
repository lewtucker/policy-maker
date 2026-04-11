# Getting Started with Policy Maker

This guide walks through using Policy Maker, from running it locally to connecting it with a live AI agent.

---

## Part 1: Running Policy Maker

### Prerequisites

- Python 3.11 or later
- An Anthropic API key (for the rule assistant — optional if you write rules manually)

### Installation

```bash
git clone https://github.com/lewtucker/policy-maker
cd policy-maker/src/server
```

Create a `.env` file:

```env
APP_PASSWORD=your-chosen-password
SESSION_SECRET=any-random-string
ANTHROPIC_API_KEY=sk-ant-...       # optional — enables the NL assistant
```

Start the server:

```bash
./start.sh
```

Or directly with uvicorn:

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8080
```

Open `http://localhost:8080` in your browser. Log in with any email address and the password you set in `.env`.

---

## Part 2: Authoring Rules

### Using the assistant (Create Rules tab)

The **Create Rules** tab has a Claude-powered chat assistant. Describe what you want in plain English:

> "Block curl and wget"
> "Allow git for anyone in the engineering group"
> "Require approval before running npm publish"

The assistant proposes a rule as an accept/discard card. Click **Accept** to add it to your policy set, or **Discard** to continue the conversation and refine it.

The assistant is aware of your existing rules and will warn you about conflicts as you go. Click **Policy Analyst** to ask for a full health evaluation of your current rule set.

### Writing rules manually (Policy Rules tab)

On the **Policy Rules** tab, click **+ Add rule** to open the rule editor. Rules follow this schema:

```yaml
id: deny-curl              # unique, kebab-case
name: Block curl           # human-readable label
description: Prevent curl  # what this rule does
result: deny               # allow | deny | pending
priority: 35               # higher = evaluated first
match:
  tool: bash               # tool name (bash, read, write, web_fetch…)
  program: curl            # first word of the shell command (bash only)
  path: /data/**           # optional glob
  person: alice            # optional short person ID
  group: engineering       # optional group name
```

All `match` fields are optional and AND-ed together. An empty `match: {}` matches everything — useful as a catch-all deny at the lowest priority.

**Priority guidance:**

| Specificity | Suggested range |
| ----------- | --------------- |
| Person + tool + program | 65–75 |
| Group + tool | 45–55 |
| Tool + program | 30–40 |
| Tool only | 15–25 |
| Catch-all | 1–5 |

### Importing an existing policy file

On the **Policy Rules** tab, use **Import YAML** to upload a `policies.yaml` file. The imported rules are merged with your existing set. You can also **Export YAML** to download your current rule set.

---

## Part 3: Testing Rules

### Test Action tab

The **Test Action** tab lets you simulate any hypothetical agent request against your rules without touching a live agent.

Fill in the Mad Libs form:

```text
[ person ]  in group  [ group ]  calls  [ tool ]  [ program ]  on path  [ path ]
```

All fields are optional — leave blank to test as an anonymous caller or without specifying a program. Click **Evaluate** to run the simulation.

The result shows:

- **Verdict block** — allow (green), deny (red), or pending (yellow), with the matching rule name
- **Evaluation trace** — every rule in priority order, with a per-condition breakdown showing exactly which conditions matched or missed

The last 5 test results are kept on screen so you can compare how different inputs evaluate without losing previous results. **Reset** clears the history and form.

### Policy health (Policy Rules tab)

The **Analyze** panel on the Policy Rules tab runs automatic checks against your full rule set:

| Check | What it means |
| ----- | ------------- |
| **shadow** | A higher-priority rule makes a lower rule unreachable |
| **conflict** | Two rules at equal priority match the same conditions but disagree on the result |
| **orphan** | A rule references a person or group that isn't in the People roster |
| **broad** | An allow rule has very few conditions — it may be more permissive than intended |
| **uncovered** | A known group has no rules targeting it at all |

---

## Part 4: Connecting a Live Agent (OpenClaw / nanoclaw)

Policy Maker can act as a real-time policy server for any AI agent that can make HTTP calls. The integration uses a single `/check` endpoint and a Bearer token.

### How the token works

Every request to `/check` carries a Bearer token in the `Authorization` header. The server uses that token to look up which user account it belongs to, then loads and evaluates **that user's** rule set. The token therefore does two things at once:

- **Authenticates** the request (only known tokens are accepted)
- **Routes** the request to the right policy set (each token maps to exactly one user's rules)

This makes Policy Maker naturally multi-user. A single shared server can govern multiple independent agent deployments simultaneously — each agent is configured with a different token, and each token owner maintains their own rules, people roster, and activity log completely independently. No agent can see or affect another user's policy.

```text
Agent A  → Bearer token-A  →  User A's rule set  →  User A's activity log
Agent B  → Bearer token-B  →  User B's rule set  →  User B's activity log
Agent C  → Bearer token-C  →  User C's rule set  →  User C's activity log
```

To add a new monitored system: create a Policy Maker account for it (any email), generate a token on the Activity tab, and give that token to the agent. Everything else — rules, people, activity — is scoped to that account automatically.

### Step 1: Generate an agent token

On the **Activity** tab, click **Generate** in the Agent Token section. Copy the token — you'll add it to your agent's configuration.

If you prefer to use your own token string, type it in the field and click **Save**.

### Step 2: Configure your agent

In your OpenClaw or nanoclaw configuration, set two environment variables:

```env
OC_POLICY_SERVER_URL=https://policy.lewtucker.net   # or your self-hosted URL
OC_POLICY_AGENT_TOKEN=<your token from Step 1>
```

The nanoclaw hook will include these automatically in every request it sends to Policy Maker.

### Step 3: Add a hook in OpenClaw / nanoclaw

In your agent's hook configuration, add the policy check hook. The hook runs before each tool call and calls Policy Maker's `/check` endpoint:

```http
POST /check
Authorization: Bearer <OC_POLICY_AGENT_TOKEN>

{
  "tool": "bash",
  "params": {
    "command": "git push origin main",
    "_caller": "tg:6741893378"
  }
}
```

The `_caller` field is the Telegram ID of the person who triggered the agent action. Policy Maker uses this to look up the person in your People roster and apply any person- or group-scoped rules.

The endpoint returns:

```json
{ "verdict": "allow", "reason": "Allowed by 'Allow git for everyone'" }
```

If verdict is `deny`, the hook should block the tool call and return an error to the agent. If `pending`, it should pause and wait for human approval.

### Step 4: Add people to the roster

For person- or group-scoped rules to work, you need to tell Policy Maker who is who. On the **Policy Rules** tab, scroll to the **People & Groups** panel and add each person:

| Field | Example | Notes |
| ----- | ------- | ----- |
| **Name** | Lew Tucker | Display name only |
| **Person ID** | lew | Short ID used in rule `person:` fields |
| **Groups** | engineering, admin | Comma-separated group names |
| **Telegram ID** | tg:6741893378 | Used to identify the caller from `_caller` in requests |

When a request arrives with `_caller: tg:6741893378`, Policy Maker looks up that Telegram ID in the roster, finds the matching person, and uses their **Person ID** (`lew`) and groups when evaluating rules. So a rule with `person: lew` will match that caller even though the raw request only contained a Telegram ID.

### Step 5: Watch the Activity tab

The **Activity** tab shows a live feed of every evaluation (polls every 10 seconds). Each row shows the timestamp, tool, a summary of parameters, and the verdict. Click the chevron to expand any row and see the full request JSON.

Use the **Clear** button to wipe the log when you want a clean slate.

---

## Example policy for an engineering team

```yaml
version: 1
policies:

  # Admins can do anything
  - id: allow-admin
    name: Allow admins
    result: allow
    priority: 90
    match:
      group: admin

  # Block destructive filesystem operations for everyone
  - id: deny-rm-rf
    name: Block recursive delete
    result: deny
    priority: 80
    match:
      tool: bash
      program: rm

  # Engineers can use git freely
  - id: allow-git-engineering
    name: Allow git for engineers
    result: allow
    priority: 50
    match:
      tool: bash
      program: git
      group: engineering

  # Require approval before publishing packages
  - id: pending-npm-publish
    name: Require approval for npm publish
    result: pending
    priority: 40
    match:
      tool: bash
      program: npm

  # Default: deny everything else
  - id: deny-all
    name: Default deny
    result: deny
    priority: 1
    match: {}
```

Rules are evaluated top-to-bottom by priority. The first match wins. An admin calling `git push` hits `allow-admin` (priority 90) and is allowed. An engineer running `rm -rf` hits `deny-rm-rf` (priority 80) and is denied before any other rule is considered.
