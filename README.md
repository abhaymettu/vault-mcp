# vault-mcp

A Model Context Protocol server that exposes a folder of markdown notes
(an Obsidian vault, or any folder of `.md` files) to MCP clients such as
Claude Desktop. Python, official `mcp` SDK, stdio transport. One source
file, no state, no index.

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
- No pagination. `limit` caps the result size; there is no cursor.
- Files that are not valid UTF-8 make `read_note` fail. Search tolerates
  them by replacing bad bytes.

## Safety

The vault root is a required command line argument. Every path a client
sends is resolved (symlinks included) and rejected unless it stays inside
the root. Requests for `../x`, absolute paths, or a symlink that points
outside the vault return an error. The directory scan behind `search_notes`,
`list_notes`, and `resources/list` applies the same check, so a symlink
that points out of the vault is never listed or searched either.

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

## Usage example

This is one run of `./demo.sh`, captured on 2026-09-10. The script writes
two notes into a temporary vault (`alpha.md` with the line "first note
about cats", `projects/beta.md` with "cats and dogs"), then pipes three
JSON-RPC messages into the server.

Sent:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_notes","arguments":{"query":"cats"}}}
```

Received, verbatim except where marked `[trimmed]`:

```json
{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"experimental":{},"prompts":{"listChanged":false},"resources":{"listChanged":false,"subscribe":false},"tools":{"listChanged":false}},"instructions":"Markdown notes vault at /private/var/folders/[trimmed]/tmp.MTSGqVho2H","protocolVersion":"2025-06-18","serverInfo":{"name":"vault-mcp","version":""}}}
{"jsonrpc":"2.0","id":2,"result":{"content":[{"text":"{\n  \"file\": \"alpha.md\",\n  \"line\": 3,\n  \"excerpt\": \"first note about cats\",\n  \"score\": 1\n}","type":"text"},{"text":"[trimmed: same shape for projects/beta.md]","type":"text"}],"isError":false,"structuredContent":{"result":[{"file":"alpha.md","line":3,"excerpt":"first note about cats","score":1},{"file":"projects/beta.md","line":3,"excerpt":"cats and dogs","score":1}]}}}
```

Things to notice:

- The notification gets no reply. Only requests with an `id` do.
- The tool result arrives twice: once as `content` (text blocks, one per
  hit, pretty-printed by the SDK) and once as `structuredContent`. Clients
  that understand output schemas use the second form.
- Both notes score 1 because each has one matching line and neither
  filename contains "cats". The tie is broken by path.
- `serverInfo.version` is empty. The server does not set one.

The full set of wire payloads, including `tools/list`, every tool, the
resources API, and the error shapes, is in [docs/protocol.md](docs/protocol.md).

## Architecture

Everything is in `server.py`. It is short enough to read in one sitting,
and the structure is:

```
Vault            file work, no MCP knowledge
  resolve()        path containment check
  notes()          directory scan (dot-files and escaping symlinks skipped)
  search() read() list() append_daily()
VaultServer      MCPServer subclass, wires Vault to the protocol
  search_notes read_note list_notes append_daily_note   tools
  note_resource                                          note://{+path}
  list_resources                                         resources/list
main()           argparse, then VaultServer(vault).run(transport="stdio")
```

### Tool implementations

Each tool is one method on `VaultServer`, registered in `__init__` with
`self.tool()(self.method)`. The SDK builds the tool's JSON schema from the
method's type hints and defaults, and uses the docstring as the description
clients see. That is why the four tool docstrings are one line each: they
are user-facing text, not developer notes.

The methods themselves are thin. Each calls the matching `Vault` method and
converts `ValueError` (path escape) and `FileNotFoundError` into `ToolError`,
which the SDK returns as a `tools/call` result with `isError: true` rather
than a JSON-RPC error. `search_notes` and `append_daily_note` cannot raise
either exception, so they do not catch anything.

Return types map to the wire like this: a `list` or `str` return is wrapped
as `{"result": ...}` in `structuredContent`, and a `dict` return is sent
as-is. `append_daily_note` returns a dict, so its `structuredContent` has
`path`, `created`, and `line` at the top level.

### Path-safety layer

`Vault.resolve` is the single choke point. It joins the client's relative
path onto the root, calls `Path.resolve()` (which follows symlinks and
collapses `..`), and raises `ValueError` unless the result is still under
the root. `read`, `list`, and `append_daily` all go through it.

`Vault.notes`, the scan used by search, list, and `resources/list`, does
not take client paths but still applies two filters per file: skip anything
with a dot-prefixed path component, and skip anything whose resolved
location is outside the root. Without the second filter a symlink such as
`link.md -> ../secret.md` would be refused by `read_note` but still have
its contents returned by `search_notes`.

Resource URIs get a third check for free. The `mcp` SDK's resource
templates reject `..` segments and absolute paths in template parameters
by default (`resource_security` in `MCPServer.__init__`), so a request for
`note://../secret.md` never reaches `note_resource`. It comes back as
"Unknown resource".

### Stdio transport

`main` calls `VaultServer.run(transport="stdio")`. In the SDK that is
`anyio.run(self.run_stdio_async)`, which opens the process's stdin and
stdout as the read and write streams and hands them to the low-level
protocol server. Messages are newline-delimited JSON-RPC 2.0. The SDK
writes its log lines (rejected arguments, failed tools and resources) to
stderr, so stdout stays clean for protocol traffic. There is no HTTP or
SSE code path in this repo, though the SDK supports both.

## Design decisions

**Substring search, not full-text or semantic search.** The use case is an
assistant looking up a note it already half-remembers: a project name, a
person, a phrase. Case-insensitive `in` on each line handles that with no
tokenizer, no stemming, and no library. The scoring rule (one point per
matching line, ten if the filename matches) is small enough to state in
one sentence and predict by hand. Fuzzy or vector search would need a
model, an index, and a way to explain results. None of that is worth it
for a personal vault.

**No index.** Every call walks the directory and reads matching files. The
alternative is an index that has to be built, stored somewhere, and kept
in sync with an editor that may write files at any time. Reading fresh
means the server is never wrong about the current state of the vault and
has no startup cost. The price is a full scan per search, which is fine
for a vault of a few thousand notes and not fine for hundreds of
thousands. That limit is documented rather than engineered around.

**`append_daily_note` is the only write.** A general `write_note` tool
would let a client overwrite any file in the vault, and the vault is the
user's primary record. Appending one timestamped line to today's daily
note is the one write that is both useful (capture a thought, log an
event) and hard to misuse: it cannot delete, cannot touch yesterday, and
cannot pick the file. The template support exists because Obsidian users
usually have a daily-note template and would otherwise get a file that
does not match the rest of the folder.

**Stdio only.** The server is meant to be launched by the client on the
same machine, with the vault path on the command line. Stdio gives that
for free: no port, no token, no TLS, and the process dies when the client
does. A network transport would need auth, and auth for a personal notes
folder is a bigger design than the rest of this server combined.

## Tool reference

Wire-level detail, including the generated JSON schemas, is in
[docs/protocol.md](docs/protocol.md).

### search_notes

Arguments: `query` (string, required), `limit` (integer, default 20).

Ranking: a note whose filename contains the query scores 10 plus its number
of matching lines. Other notes score by matching line count. All hits from
one note share that note's score. Hits are sorted by score, then path, then
line number. `excerpt` is the matching line, trimmed to 200 characters. A
note that matches only by filename yields one hit on line 1. An empty
query returns an empty list.

```json
{"name": "search_notes", "arguments": {"query": "cats", "limit": 5}}
```

Result (from the demo run above):

```json
[
  {"file": "alpha.md", "line": 3, "excerpt": "first note about cats", "score": 1},
  {"file": "projects/beta.md", "line": 3, "excerpt": "cats and dogs", "score": 1}
]
```

### read_note

Arguments: `path` (string, required, relative to the vault root).

```json
{"name": "read_note", "arguments": {"path": "projects/beta.md"}}
```

Result: the file contents as text. A missing file or a path outside the
vault returns a tool error with `isError: true`.

### list_notes

Arguments: `folder` (string, default `""` for the whole vault),
`limit` (integer, default 100).

```json
{"name": "list_notes", "arguments": {"folder": "projects", "limit": 10}}
```

Result:

```json
[
  {"path": "projects/beta.md", "modified": "2026-09-10T11:07:57"}
]
```

`modified` is local time at second precision.

### append_daily_note

Arguments: `text` (string, required).

```json
{"name": "append_daily_note", "arguments": {"text": "called the dentist"}}
```

Result:

```json
{"path": "daily/2026-09-10.md", "created": true, "line": "- 11:08 called the dentist"}
```

`text` is stripped of surrounding whitespace. A newline inside `text`
produces extra lines without the `- HH:MM` prefix.

### Resources

`resources/list` returns one entry per note with URI `note://<path>`,
sorted by path. `resources/templates/list` returns the single template
`note://{+path}`. `resources/read` with a note URI returns the note as
`text/markdown`. Errors on the resources API are JSON-RPC error objects,
not `isError` results.

## Tests

```
.venv/bin/python -m pytest
```

The suite builds a temporary vault and covers each tool, path escape
refusal (`..`, absolute paths, and symlinks, for both direct reads and
the directory scan), daily note creation with and without a template, and
a full stdio round trip through the real client library.

## Layout

- `server.py`: the whole server. `Vault` does file work, `VaultServer`
  wires it to MCP.
- `tests/test_server.py`: the test suite.
- `docs/protocol.md`: wire payloads from a real run.
- `setup.sh`, `demo.sh`, `requirements.txt`.
