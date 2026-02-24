# Webflow MCP client config snippets

## Cursor (remote OAuth)

```json
{
  "mcpServers": {
    "webflow": {
      "url": "https://mcp.webflow.com/sse"
    }
  }
}
```

## Claude Desktop (remote OAuth)

```json
{
  "mcpServers": {
    "webflow": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.webflow.com/sse"]
    }
  }
}
```

## Local mode with token

```json
{
  "mcpServers": {
    "webflow": {
      "command": "npx",
      "args": ["-y", "webflow-mcp-server@latest"],
      "env": {
        "WEBFLOW_TOKEN": "<YOUR_WEBFLOW_TOKEN>"
      }
    }
  }
}
```

## Notes

- Node.js 22.3.0+ is required by the official Webflow MCP server.
- For Designer control, publish and run the Webflow MCP Bridge App in Webflow Designer.
- If auth gets stuck, remove OAuth cache directory: `~/.mcp-auth`.
