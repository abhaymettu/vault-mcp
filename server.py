"""vault-mcp: expose a local markdown vault to MCP clients over stdio."""

import argparse
import datetime as dt
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import Resource

DAILY_FOLDER = "daily"
TEMPLATE_PATH = "templates/daily.md"


class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)

    def resolve(self, rel: str) -> Path:
        """Resolve a vault-relative path; refuse anything outside the root."""
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"path escapes vault root: {rel}")
        return target

    def notes(self, folder: str = ""):
        base = self.resolve(folder)
        for p in base.rglob("*.md"):
            rel = p.relative_to(self.root)
            if not any(part.startswith(".") for part in rel.parts):
                yield p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        q = query.lower()
        if not q:
            return []
        hits: list[dict] = []
        for p in self.notes():
            path = self.rel(p)
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            file_hits = [(i, line) for i, line in enumerate(lines, 1) if q in line.lower()]
            name_match = q in p.stem.lower()
            if not file_hits and not name_match:
                continue
            score = len(file_hits) + (10 if name_match else 0)
            if name_match and not file_hits:
                file_hits = [(1, lines[0] if lines else "")]
            for i, line in file_hits:
                hits.append({"file": path, "line": i, "excerpt": line.strip()[:200], "score": score})
        hits.sort(key=lambda h: (-h["score"], h["file"], h["line"]))
        return hits[:limit]

    def read(self, rel: str) -> str:
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_text(encoding="utf-8")

    def list(self, folder: str = "", limit: int = 100) -> list[dict]:
        items = [(p.stat().st_mtime, p) for p in self.notes(folder)]
        items.sort(key=lambda t: -t[0])
        return [
            {"path": self.rel(p), "modified": dt.datetime.fromtimestamp(m).isoformat(timespec="seconds")}
            for m, p in items[:limit]
        ]

    def append_daily(self, text: str, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now()
        day = now.strftime("%Y-%m-%d")
        p = self.resolve(f"{DAILY_FOLDER}/{day}.md")
        created = not p.exists()
        if created:
            p.parent.mkdir(parents=True, exist_ok=True)
            tpl = self.root / TEMPLATE_PATH
            body = tpl.read_text(encoding="utf-8") if tpl.is_file() else "# {{date}}\n\n"
            p.write_text(body.replace("{{date}}", day), encoding="utf-8")
        line = f"- {now.strftime('%H:%M')} {text.strip()}\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        return {"path": self.rel(p), "created": created, "line": line.rstrip("\n")}


class VaultServer(MCPServer):
    def __init__(self, vault: Vault):
        super().__init__("vault-mcp", instructions=f"Markdown notes vault at {vault.root}")
        self.vault = vault
        self.tool()(self.search_notes)
        self.tool()(self.read_note)
        self.tool()(self.list_notes)
        self.tool()(self.append_daily_note)
        self.resource("note://{+path}", mime_type="text/markdown")(self.note_resource)

    def search_notes(self, query: str, limit: int = 20) -> list[dict]:
        """Case-insensitive substring search across all notes. Returns ranked hits with file, line, excerpt."""
        return self.vault.search(query, limit)

    def read_note(self, path: str) -> str:
        """Read one note. `path` is relative to the vault root, e.g. 'projects/foo.md'."""
        try:
            return self.vault.read(path)
        except (ValueError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e

    def list_notes(self, folder: str = "", limit: int = 100) -> list[dict]:
        """List notes, newest modified first. `folder` narrows to a subfolder."""
        try:
            return self.vault.list(folder, limit)
        except ValueError as e:
            raise ToolError(str(e)) from e

    def append_daily_note(self, text: str) -> dict[str, str | bool]:
        """Append a timestamped line to today's daily note (daily/YYYY-MM-DD.md), creating it if missing."""
        return self.vault.append_daily(text)

    def note_resource(self, path: str) -> str:
        try:
            return self.vault.read(path)
        except (ValueError, FileNotFoundError) as e:
            raise ResourceError(str(e)) from e

    async def list_resources(self) -> list[Resource]:
        return [
            Resource(uri=f"note://{self.vault.rel(p)}", name=self.vault.rel(p), mime_type="text/markdown")
            for p in sorted(self.vault.notes())
        ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP server for a markdown notes vault (stdio).")
    ap.add_argument("vault", help="path to the vault root directory")
    args = ap.parse_args(argv)
    try:
        vault = Vault(Path(args.vault))
    except NotADirectoryError as e:
        sys.exit(f"vault-mcp: not a directory: {e}")
    VaultServer(vault).run(transport="stdio")


if __name__ == "__main__":
    main()
