# Homey MCP Integration

HomeAgent connects to your Homey smart home via the **Homey AI Chat Control** app — a local MCP server that runs directly on your Homey unit and is reachable over your LAN. No cloud relay, no OAuth, no tokens required.

Reference: [Homey AI Chat Control documentation](https://jvmenen.github.io/homey-ai-chat-control/)

---

## What MCP Gives Us

The local MCP server exposes your Homey devices, flows, and zones as AI-callable tools. The agent can:

- **Query device state**: "Is the front door locked?", "What's the temperature in the living room?"
- **Control devices**: Turn lights on/off, set brightness/colour, adjust thermostat, lock/unlock
- **Trigger flows**: Run any Homey flow ("Start movie night mode", "Goodnight routine")
- **Query zones**: Get all devices in a room, check what's active
- **Historical data**: Temperature, energy, and sensor readings over time

The available tools are dynamically discovered at agent startup via the MCP protocol.

---

## Step 1: Install the Homey AI Chat Control App

1. Go to the Homey App Store and search for **"Homey AI Chat Control"**
2. Install the app on your Homey unit
3. Once installed, the app starts an MCP server on port **3000**
4. Verify it's running by opening a browser on your LAN:
   `http://<your-homey-ip>:3000/health`

---

## Step 2: Find Your Homey's LAN IP

- Open the **Homey** mobile app → Settings → General → Network
- Or check your router's DHCP client list for the device named "Homey"
- The IP will look like `192.168.1.42` — assign a static DHCP lease if possible so it doesn't change

---

## Step 3: Configure .env

```env
HOMEY_MCP_URL=http://192.168.1.42:3000/mcp
```

That's it. No token, no client ID, no secret. The app relies on LAN-level security — only devices on your home network can reach it.

---

## How the Agent Connects

HomeAgent uses **Pydantic AI's `MCPServerStreamableHTTP`** to connect to the local Homey MCP server at startup. The MCP protocol lets the agent:

1. **Discover tools** — queries the MCP server for the full list of available tools (devices, flows, zones). These are registered as callable tools prefixed with `homey_`.
2. **Call tools** — during conversation, the agent calls MCP tools to read state or control devices.
3. **Receive results** — tool results are returned to the agent and included in its response.

The connection is plain HTTP over LAN — no TLS, no auth headers.

---

## Confirmation for Sensitive Actions

High-impact Homey actions (locks, alarms, shutoff valves) require explicit Telegram confirmation before executing. This is governed by the **Policy Gate** — see [docs/policy-gate.md](../policy-gate.md).

Read-only queries and routine device adjustments execute immediately without prompting.

---

## Troubleshooting

**`HOMEY_MCP_URL` not reachable:**
1. Confirm the Homey AI Chat Control app is installed and running
2. Test from the HomeAgent machine: `curl http://192.168.1.42:3000/health`
3. Check that your firewall isn't blocking port 3000
4. Ensure HomeAgent and Homey are on the same LAN (or VLAN with routing)

**Agent says "smart home tools disabled":**
Check that `HOMEY_MCP_URL` is set in `.env` and points to the correct IP/port.

**Tool calls failing:**
The available tools depend on which Homey apps and devices you have. Ask the agent: "What Homey tools do you have?" to see the discovered tool list.

**IP address changed:**

Assign a static DHCP lease to your Homey in your router settings. Use the MAC address shown in Homey → Settings → General → Network.

---

## Security Notes

- The MCP server is HTTP-only and unauthenticated — **do not expose port 3000 to the internet**
- Keep HomeAgent on your private LAN (or run it on the same machine as your home server)
- If you use Tailscale for remote access to HomeAgent, Homey's port 3000 is not exposed externally via that path — only HomeAgent itself is tunnelled
