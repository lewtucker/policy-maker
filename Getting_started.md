# Getting Started with Policy Maker

This guide covers everything you need to get up and running with Policy Maker: using the web UI, creating and testing rules, running the server locally, and deploying to a remote server to govern a live OpenClaw agent deployment.

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

#### The skill prompt

The assistant's behaviour is driven by a **skill prompt** — a system prompt that tells Claude how to interpret requests, what rule schema to follow, and how to format proposals. You can view, download, and replace it using the controls in the sidebar panel on the Create Rules tab.

- **Download** — saves the current skill as `skill.txt` so you can review or version-control it
- **Upload** — replaces the skill with your own customised version
- **Reset** — reverts to the built-in default skill

This makes the assistant fully customisable. If you want it to enforce stricter naming conventions, speak a different language, apply domain-specific guidance, or change how it prioritises rules, edit the skill prompt and upload it. Your customised skill is stored per-user and persists across sessions.

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

**Wildcard and multi-value behaviour:**

| Field | Wildcards | Multiple values |
| ----- | --------- | --------------- |
| `path` | `*` and `**` via `fnmatch` — both match any characters including `/` so `/foo/*.txt` also matches `/foo/sub/file.txt` | Not supported |
| `program` | `*` and `**` via `fnmatch` | Not supported |
| `tool` | Not supported — exact match only | Not supported |
| `person` | Not supported — exact match only | Not supported |
| `group` | Not supported — exact match only | Not supported |

To match multiple tools or programs, write a separate rule for each one.

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

---

## Part 5: Running Policy Maker on a Remote Server

This section covers deploying Policy Maker to a Linux VPS (the production instance at `policy.lewtucker.net` runs on Hostinger) with automatic deploys via GitHub Actions.

### Server prerequisites

- A Linux VPS (Ubuntu 22.04 or later recommended)
- Python 3.11+ installed
- `systemd` for process management
- SSH access as root (or a user with sudo)
- A domain name pointed at the server's IP (optional but recommended)

### Step 1: Copy the code to the server

SSH into your VPS and clone the repository:

```bash
ssh root@<your-server-ip>
git clone https://github.com/lewtucker/policy-maker /opt/policy-maker
```

### Step 2: Create the `.env` file

The `.env` file lives on the server only — it is never committed or deployed by CI:

```bash
cat > /opt/policy-maker/src/server/.env <<EOF
APP_PASSWORD=your-chosen-password
SESSION_SECRET=any-long-random-string
ANTHROPIC_API_KEY=sk-ant-...
EOF
```

### Step 3: Install dependencies

```bash
cd /opt/policy-maker/src/server
pip install -r requirements.txt
```

### Step 4: Create a systemd service

Create `/etc/systemd/system/policy-maker.service`:

```ini
[Unit]
Description=Policy Maker
After=network.target

[Service]
WorkingDirectory=/opt/policy-maker/src/server
ExecStart=/usr/local/bin/uvicorn server:app --host 0.0.0.0 --port 8080
Restart=always
EnvironmentFile=/opt/policy-maker/src/server/.env

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
systemctl daemon-reload
systemctl enable policy-maker
systemctl start policy-maker
```

The server is now running on port 8080. If you have a reverse proxy (nginx, Caddy) handling HTTPS, point it at `localhost:8080`.

### Step 5: Set up automatic deploys with GitHub Actions

The repository ships a workflow (`.github/workflows/deploy.yml`) that rsyncs `src/server/` to the VPS and restarts the service on every push to `main` that touches `src/`.

You need to add two secrets to your GitHub repository (**Settings → Secrets and variables → Actions**):

| Secret | Value |
| ------ | ----- |
| `DEPLOY_KEY` | Base64-encoded SSH private key that has access to the server |

Generate a dedicated deploy key pair (no passphrase):

```bash
ssh-keygen -t ed25519 -f deploy_key -N ""
```

Add the public key to the server:

```bash
cat deploy_key.pub >> ~/.ssh/authorized_keys
```

Base64-encode the private key and paste it as the `DEPLOY_KEY` secret:

```bash
base64 < deploy_key | pbcopy   # macOS — pastes into clipboard
```

From this point on, every push to `main` that changes a file under `src/` will:

1. Rsync the updated code to `/opt/policy-maker/src/server/` (skipping `.env` and `policy_maker.db` so server state is preserved)
2. Run `systemctl restart policy-maker`

Pushes that only touch documentation are skipped automatically.
