# vault-mcp

A Model Context Protocol server that exposes a folder of markdown notes
(an Obsidian vault, or any folder of `.md` files) to MCP clients such as
Claude Desktop. Python, official `mcp` SDK, stdio transport.

## What it does

- `search_notes`: case-insensitive substring search across every note.
  Returns hits with file, line number, and the matching line.
- `read_note`: returns the text of one note by vault-relative path.
- `list_notes`: lists notes, newest modified first, optionally within one folder.
- `append_daily_note`: appends a timestamped line to today's daily note at
  `daily/YYYY-MM-DD.md`. Creates the file if missing, from
  `templates/daily.md` if that exists (with `{{date}}` replaced), otherwise
  from a one-line heading.
- Resources: every note is listed as `note://<relative path>` and can be read
  through the MCP resources API.

Files and folders whose name starts with a dot (such as `.obsidian`) are
skipped. Only `.md` files are listed or searched.

## What it does not do

- No semantic or fuzzy search. It is plain substring matching.
- No watch mode. Every call reads the disk fresh, so there is no index to
  go stale, but large vaults are scanned on every search.
- No writes other than `append_daily_note`, and that tool only touches
  today's daily note.
- No network transport. Stdio only.
- No auth. Anything that can launch the process can read the vault.

## Safety

The vault root is a required command line argument. Every path a client
sends is resolved (symlinks included) and rejected unless it stays inside
the root. Requests for `../x`, absolute paths, or a symlink that points
outside the vault return an error.

## Install

Requires Python 3.11 or newer.

```
git clone <this repo> vault-mcp
cd vault-mcp
./setup.sh
```

`setup.sh` creates `.venv`, installs pinned dependencies from
`requirements.txt`, runs the test suite, and prints the usage line. It is
safe to run again.

Run by hand:

```
.venv/bin/python server.py /path/to/vault
```

The server speaks JSON-RPC on stdin and stdout, so it will sit waiting for
input. `./demo.sh` shows the full handshake: it starts the server on a
throwaway vault, sends `initialize`, `notifications/initialized`, and one
`tools/call`, and prints the raw replies.

## Register with Claude Desktop

Add this to `claude_desktop_config.json` (on macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`).
Replace both paths with your own.

```json
{
  "mcpServers": {
    "vault": {
      "command": "/Users/you/code/vault-mcp/.venv/bin/python",
      "args": [
        "/Users/you/code/vault-mcp/server.py",
        "/Users/you/Documents/MyVault"
      ]
    }
  }
}
```

Restart Claude Desktop after saving.

## Tool reference

### search_notes

Arguments: `query` (string, required), `limit` (integer, default 20).

Ranking: a note whose filename contains the query scores 10 plus its number
of matching lines. Other notes score by matching line count. Hits are
sorted by score, then path, then line number. `excerpt` is the matching
line, trimmed to 200 characters.

```json
{"name": "search_notes", "arguments": {"query": "cats", "limit": 5}}
```

Result:

```json
[
  {"file": "projects/beta.md", "line": 3, "excerpt": "cats and dogs", "score": 2},
  {"file": "alpha.md", "line": 3, "excerpt": "first note about cats", "score": 1}
]
```

### read_note

Arguments: `path` (string, required, relative to the vault root).

```json
{"name": "read_note", "arguments": {"path": "projects/beta.md"}}
```

Result: the file contents as text. Missing file or a path outside the
vault returns a tool error.

### list_notes

Arguments: `folder` (string, default `""` for the whole vault),
`limit` (integer, default 100).

```json
{"name": "list_notes", "arguments": {"folder": "projects", "limit": 10}}
```

Result:

```json
[
  {"path": "projects/beta.md", "modified": "2026-09-10T10:41:00"}
]
```

### append_daily_note

Arguments: `text` (string, required).

```json
{"name": "append_daily_note", "arguments": {"text": "called the dentist"}}
```

Result:

```json
{"path": "daily/2026-09-10.md", "created": true, "line": "- 10:41 called the dentist"}
```

### Resources

`resources/list` returns one entry per note with URI `note://<path>`.
`resources/read` with that URI returns the note as `text/markdown`.

## Tests

```
.venv/bin/python -m pytest
```

The suite builds a temporary vault and covers each tool, path escape
refusal (including symlinks), daily note creation with and without a
template, and a full stdio round trip through the real client library.

## Layout

- `server.py`: the whole server. `Vault` does file work, `VaultServer`
  wires it to MCP.
- `tests/test_server.py`: the test suite.
- `setup.sh`, `demo.sh`, `requirements.txt`.
