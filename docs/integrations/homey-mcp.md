# Homey MCP Integration

HomeAgent connects to your Homey smart home via Homey's official **MCP (Model Context Protocol) server**. This exposes all your Homey devices, flows, and capabilities as tools the AI agent can call.

Reference: [Homey MCP Server announcement](https://homey.app/en-us/news/introducing-the-homey-mcp-server/)

---

## What MCP Gives Us

The Homey MCP server exposes your entire home as a set of AI-callable tools. The agent can:

- **Query device state**: "Is the front door locked?", "What's the temperature in the living room?"
- **Control devices**: Turn lights on/off, set brightness/colour, adjust thermostat, lock/unlock
- **Trigger flows**: Run any Homey flow ("Start movie night mode", "Goodnight routine")
- **Query zones**: Get all devices in a room, check what's active

The exact tools available depend on which devices and apps you have in Homey. They are dynamically discovered at agent startup via the MCP protocol.

---

## Step 1: Enable the Homey MCP Server

1. Go to [homey.app](https://homey.app) and log in
2. Navigate to **Account → Developers** (or follow the link from the announcement)
3. Enable the **MCP Server** for your home
4. Note the **MCP Server URL** — it will look like:
   `https://mcp.homey.app/mcp/<your-home-id>`

> **Note:** The Homey MCP server runs in Homey's cloud. Your HomeAgent server calls out to it over HTTPS. Your Homey hub does not need to be directly reachable from the internet.

---

## Step 2: Generate a Personal Access Token

1. In Homey web app, go to **Account → Security → Personal Access Tokens**
2. Click **Create Token**
3. Give it a descriptive name (e.g. "HomeAgent")
4. Set appropriate scopes — at minimum:
   - `devices.read`
   - `devices.control`
   - `flows.read`
   - `flows.start`
   - `zones.read`
5. Copy the token — it is only shown once

---

## Step 3: Find Your Home ID

Your home ID is visible in the Homey web app URL when you are viewing your home, or in the MCP server URL from Step 1.

It looks like a UUID: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`

---

## Step 4: Configure .env

```env
HOMEY_MCP_URL=https://mcp.homey.app/mcp/<your-home-id>
HOMEY_TOKEN=<your-personal-access-token>
HOMEY_HOME_ID=<your-home-id>
```

---

## How the Agent Connects

HomeAgent uses **Pydantic AI's MCP client** to connect to the Homey MCP server at startup. The MCP protocol allows the agent to:

1. **Discover tools** — at startup, the agent queries the MCP server for the full list of available tools (all devices, capabilities, flows). These are registered as callable tools.
2. **Call tools** — during conversation, when the agent decides to control a device, it calls the corresponding MCP tool.
3. **Receive results** — tool results (success/failure, current state) are returned to the agent and factored into its response.

The MCP connection is authenticated via the Personal Access Token, passed as a Bearer token in the HTTP Authorization header.

---

## What Tools Look Like

After connecting, the agent will have tools like:

```
homey_device_get_state(device_id: str) → DeviceState
homey_device_set_capability(device_id: str, capability: str, value: Any) → Result
homey_flow_trigger(flow_id: str) → Result
homey_zone_get_devices(zone_id: str) → List[Device]
```

The exact tool names and signatures are determined by the Homey MCP server's schema. The agent is told about all available tools in its system context.

---

## Natural Language to Tool Call

The agent handles the translation from natural language to the correct MCP tool call:

> User: "Dim the living room lights to 40%"
>
> Agent:
> 1. Identifies intent: set brightness
> 2. Looks up "living room" zone → finds light devices
> 3. Calls `homey_device_set_capability` for each light with `brightness = 0.4`
> 4. Confirms result: "Done, living room lights dimmed to 40%"

Device names and zone names in Homey are visible to the agent via the home profile and MCP tool descriptions. Teach the agent your device names by using them naturally in conversation — it will remember them.

---

## Home Profile and Device Discovery

At startup (and periodically), HomeAgent queries the MCP server to build a **home profile** — a structured map of zones, devices, and capabilities. This is stored in the household profile (see [memory-design.md](../memory-design.md)) and injected into the agent's context for home-related queries.

The home profile includes:
- Zones (rooms) and their names
- Devices per zone with their capabilities
- Available flows

This means the agent knows your home layout without you having to describe it.

---

## Confirmation for Sensitive Actions

Which Homey actions require user confirmation before executing is governed entirely by the **Policy Gate** — a declarative policy table in SQLite. There is no separate `guardrails.py` or per-integration hardcoded list.

For the default policy set and how to add or modify policies, see [docs/policy-gate.md](../policy-gate.md).

In summary: read-only queries and single-device adjustments execute immediately. Whole-home changes, security devices, locks, and cross-user actions require explicit confirmation via Telegram inline button.

---

## Multiple Homes (future)

Homey supports multiple homes (e.g. main house + cabin) under one account. Each home has its own MCP URL and home ID.

Current implementation: single home. Future: the agent will detect which home the user is asking about from context, or ask for clarification, and route the MCP call accordingly.

Planned configuration:
```env
HOMEY_HOME_MAIN_URL=https://mcp.homey.app/mcp/<main-home-id>
HOMEY_HOME_CABIN_URL=https://mcp.homey.app/mcp/<cabin-home-id>
```

---

## Troubleshooting

**Agent can't connect to Homey MCP:**
1. Check `HOMEY_MCP_URL` is correct (includes your home ID)
2. Verify `HOMEY_TOKEN` is valid and not expired
3. Check token scopes include `devices.read` and `devices.control`
4. Verify the MCP server is enabled in your Homey account

**Device not found / wrong name:**
Homey uses your device names exactly as set in the Homey app. If you refer to a device by a different name, the agent may not match it. Correct device names are visible in the home profile. You can ask the agent: "What devices do you know about in the living room?"

**Flow not triggering:**
Ensure the flow is enabled in Homey and that the token has `flows.start` scope.

**Stale home profile:**
The home profile is refreshed at startup and every 24 hours. If you add new devices, restart HomeAgent or ask it to "refresh the home profile".

---

## Security Notes

- The Personal Access Token grants control over your home — treat it like a password
- Store it only in `.env`, never commit it to git
- If compromised, revoke it immediately in Homey account settings and generate a new one
- HomeAgent communicates with Homey over HTTPS only
- No local network access to Homey hub is required
