# Integrating Policy Maker with OpenClaw

Policy Maker can act as a real-time policy server for any AI agent that can make HTTP calls. The integration uses a single `/check` endpoint and a Bearer token.

## How the token works

Every request to `/check` carries a Bearer token in the `Authorization` header. The server uses that token to look up which user account it belongs to, then loads and evaluates **that user's** rule set. The token therefore does two things at once:

- **Authenticates** the request (only known tokens are accepted)
- **Routes** the request to the right policy set (each token maps to exactly one user's rules)

This makes Policy Maker naturally multi-user. A single shared server can govern multiple independent agent deployments simultaneously — each agent is configured with a different token, and each token owner maintains their own rules, people roster, and activity log completely independently. No agent can see or affect another user's policy.

```text
Agent A  → Bearer token-A  →  User A's rule set  →  User A's activity log
Agent B  → Bearer token-B  →  User B's rule set  →  User B's activity log
Agent C  → Bearer token-C  →  User C's rule set  →  User C's activity log
```

To add a new monitored system: create a Policy Maker account for it (any email), generate a token on the **Profile** page, and give that token to the agent. Everything else — rules, people, activity — is scoped to that account automatically.

---

## Step 1: Generate an agent token

Click your **email address** in the bottom-left of the sidebar to open your Profile page. Scroll to the **Agent Token** section and click **Generate**. Copy the token — you'll add it to your agent's configuration.

If you prefer to use your own token string, type it into the field and click **Save**.

---

## Step 2: Install the OpenClaw plugin

OpenClaw has two extension mechanisms — **skills** (Markdown + scripts) and **plugins** (TypeScript modules). Policy enforcement requires a **plugin** because only plugins can register a `before_tool_call` hook that intercepts and blocks tool execution before it runs.

### Plugin files

The plugin lives in three files:

```text
oc-policy/
├── openclaw.plugin.json    # Manifest — plugin id, config schema
├── package.json            # Package metadata
└── src/
    └── index.ts            # Hook implementation
```

### Where to put them

Copy the plugin directory into OpenClaw's extensions folder. Inside the container (or your local `~/.openclaw/` data directory) the path is:

```text
~/.openclaw/extensions/oc-policy/
├── openclaw.plugin.json
├── package.json
└── src/
    └── index.ts
```

If you are running OpenClaw in Docker (the typical setup), the `~/.openclaw/` directory is bind-mounted from your host workspace — for example `~/OC2/workspace/data/`. Copy the files there:

```bash
cp -r oc-policy/ ~/OC2/workspace/data/extensions/oc-policy/
```

OpenClaw uses **jiti** to transpile TypeScript at runtime — no build step is needed.

### How the plugin works

On every tool call the plugin:

1. Sends `POST /check` to your Policy Maker server with the tool name, params, and caller identity
2. If verdict is `allow` → lets the tool proceed
3. If verdict is `deny` → blocks the tool call and returns the reason to the agent
4. If verdict is `pending` → polls `GET /approvals/{id}` every 500 ms for up to 2 minutes, then blocks on timeout
5. If the server is **unreachable** → **fails closed** (blocks the tool call)

---

## Step 3: Configure the plugin

Enable the plugin in your `openclaw.json` under `plugins.entries`, pointing it at your Policy Maker server URL and the token you generated in Step 1:

```json
{
  "plugins": {
    "entries": {
      "oc-policy": {
        "enabled": true,
        "config": {
          "policyServerUrl": "https://policy.lewtucker.net",
          "agentToken": "<your token from Step 1>",
          "approvalTimeoutMs": 120000,
          "channelId": null
        }
      }
    }
  }
}
```

Replace `https://policy.lewtucker.net` with wherever your Policy Maker server is running — this can be a public URL, a local address, a Tailscale hostname, or anything the OpenClaw container can reach over the network.

Alternatively, set environment variables instead of editing `openclaw.json`:

```env
OC_POLICY_SERVER_URL=https://policy.lewtucker.net
OC_POLICY_AGENT_TOKEN=<your token from Step 1>
```

The plugin reads the env vars as a fallback if no `config` block is present.

### The `channelId` field

`channelId` is the Telegram ID of the person running this OpenClaw instance (e.g. `tg:6741893378`). When set, Policy Maker uses it to look up the caller in your People roster and applies any person- or group-scoped rules. Leave it `null` to skip per-person identity resolution — all requests will be evaluated without a subject.

### Restart the gateway

After copying the plugin files and updating `openclaw.json`, restart the OpenClaw gateway for the changes to take effect:

```bash
docker compose restart openclaw-gateway
```

Verify the plugin loaded successfully:

```bash
docker logs openclaw-gateway --tail 50 | grep oc-policy
# Should show: [oc-policy] Enforcement active — server: https://policy.lewtucker.net
```

---

## Step 4: Add people to the roster

For person- or group-scoped rules to work, you need to tell Policy Maker who is who. On the **Policy Rules** tab, scroll to the **People & Groups** panel and add each person:

| Field | Example | Notes |
| ----- | ------- | ----- |
| **Name** | Lew Tucker | Display name only |
| **Person ID** | lew | Short ID used in rule `person:` fields |
| **Groups** | engineering, admin | Comma-separated group names |
| **Telegram ID** | tg:6741893378 | Used to identify the caller from `_caller` or `channelId` in requests |

When a request arrives with `_caller: tg:6741893378` (nanoclaw) or `channelId: tg:6741893378` (OpenClaw), Policy Maker looks up that Telegram ID in the roster, finds the matching person, and uses their **Person ID** (`lew`) and groups when evaluating rules. So a rule with `person: lew` will match that caller even though the raw request only contained a Telegram ID.

Policy Maker also accepts the caller's name directly if no Telegram ID is available — it tries to match the `_caller` value against the person_id, full name, and first word of name in order.

---

## Step 5: Watch the Activity tab

The **Activity** tab shows a live feed of every evaluation (polls every 10 seconds). Each row shows the timestamp, tool, a summary of parameters, and the verdict. Click the chevron to expand any row and see the full request JSON.

Use the **Clear** button to wipe the log when you want a clean slate.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| All tool calls blocked, logs show "unreachable" | Policy Maker not running, or wrong URL | Check `policyServerUrl` in config; verify the server is reachable from the container |
| Plugin not loaded (no `[oc-policy]` in logs) | Files not in extensions dir, or not enabled | Confirm files exist at `~/.openclaw/extensions/oc-policy/`; check `enabled: true` in `openclaw.json` |
| 401 from policy server | Wrong agent token | Ensure `agentToken` in config matches the token shown on your Profile page |
| Identity not resolved (rules with `person:` don't match) | `channelId` not set, or person not in roster | Set `channelId` in plugin config and add the person to the People & Groups roster |
| Tool calls not appearing in Activity tab | Plugin loaded but hook not firing | Check gateway logs for errors after restarting |
