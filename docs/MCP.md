# Connect an AI agent to LocalBook (MCP)

LocalBook runs an [MCP](https://modelcontextprotocol.io) server inside the Mac app, so agents such
as Claude Code, Claude Desktop, Cursor or your own scripts can search and read your notebooks.

- **Where:** `http://127.0.0.1:8000/mcp` (Streamable HTTP). It answers only to programs on this Mac.
  Requests from web pages are refused.
- **When:** only while LocalBook is open.
- **Who:** each agent gets its own **agent key**. You can see what every agent called and revoke
  any key without affecting the others.

## 1. Issue a key

LocalBook → **Settings → Companions → Agent keys**.

1. Name the agent, for example `claude-code` or `claude-desktop`.
2. Tick **mcp**. Tick **all_notebooks** only if the agent may search every notebook in one call.
   Without it, the agent works one notebook at a time, which is how LocalBook's own chat works.
   (Only @curator looks across all of them.)
3. Click **Issue key** and copy it. It is shown once. LocalBook keeps only a hash of it.

The screen then shows ready-to-paste setup for the clients below, with your key already filled in.

## 2a. Claude Code

```bash
claude mcp add --transport http --scope user localbook http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer lb-YOUR-KEY"
```

Check it with `claude mcp list`, or `/mcp` inside Claude Code.

## 2b. Claude Desktop

Claude Desktop starts local servers as programs, and its config file does not take a URL, so
LocalBook ships a small Claude Desktop extension that passes messages between them. It uses the
Node that comes with Claude Desktop, so you don't need to install anything else.

```bash
git pull origin dev
./mcp-bridge/build.sh          # writes mcp-bridge/dist/LocalBook.mcpb
open mcp-bridge/dist/LocalBook.mcpb
```

Claude Desktop opens the extension. Click **Install**, paste your agent key when it asks, and
turn it on. Claude Desktop keeps the key in the macOS Keychain.

If you'd rather edit `claude_desktop_config.json` yourself (Settings → Developer → Edit Config),
the extension's script also works on its own. It needs Node 18 or newer on your PATH:

```json
{
  "mcpServers": {
    "localbook": {
      "command": "node",
      "args": ["/path/to/LocalBook/mcp-bridge/server/index.js"],
      "env": { "LOCALBOOK_MCP_KEY": "lb-YOUR-KEY" }
    }
  }
}
```

## 2c. Anything else that speaks MCP over HTTP

```json
{
  "mcpServers": {
    "localbook": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer lb-YOUR-KEY" }
    }
  }
}
```

A client that only speaks stdio can run `node mcp-bridge/server/index.js` with
`LOCALBOOK_MCP_KEY` set.

## What an agent can do

Start here, one notebook at a time:

| Tool | What it does |
|---|---|
| `list_notebooks` | Your notebooks, with ids and source counts |
| `search_notebook` | Search one notebook, best passages first. It uses the same retrieval as LocalBook's chat (keyword and meaning search, then the reranker) but doesn't run the model, so it's fast and is never turned away as busy |
| `list_sources` / `get_source` | A notebook's sources, and one source's full text a page at a time |
| `list_notes` / `get_note` | The notes in a notebook, and one note in full |
| `ask_notebook` | A written, cited answer from LocalBook's own model. It's slower, and it returns `busy` while you are using the model yourself |

Also available: `list_recent_notes`, `todays_digest`, `curator_insights`, `approval_queue`,
`web_search`, `fetch_page`, `youtube_search`, `scholarly_search`, `start_research` / `get_job`.
With the `events` scope you also get `events_since` and `event_kinds`, and with the `memory` scope
`memory_search` and `memory_add`. Searching more than one notebook at once (`search_notebooks`, or
`ask_notebook` with no notebook named) needs `all_notebooks`.

An agent can't change your notebooks. `propose_note` puts a suggestion in the Collector's approval
queue, and you decide in LocalBook whether it goes in. Sync isn't involved.

## If it doesn't connect

| You see | Meaning |
|---|---|
| "LocalBook is not running on this Mac" | Open LocalBook. The extension reconnects on its own, even after LocalBook restarts. |
| 401 "Invalid companion key" | The key is wrong, or was replaced when a new one was issued for that name. Issue a new one. |
| 403 "does not carry the 'mcp' scope" | Issue the key again with **mcp** ticked. |
| "does not carry the 'all_notebooks' scope" | The agent tried to search every notebook. Name a notebook, or issue the key with **all_notebooks**. |
| 403 "answers only to 127.0.0.1 or localhost" | Use `127.0.0.1` or `localhost` in the URL, not the Mac's network name. |

The **What they have done** list under Agent keys shows each call, its result and how long it
took. Arguments are hashed, so your questions are never written down. The backend log is at
`~/Library/Logs/LocalBook/backend.log`.
