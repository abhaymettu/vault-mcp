"""vault-mcp: expose a local markdown vault to MCP clients over stdio.

Two classes and one entry point:

- ``Vault`` does all file work. It knows nothing about MCP.
- ``VaultServer`` is an ``MCPServer`` subclass that registers the four tools
  and the ``note://`` resource, and maps ``Vault`` errors to MCP errors.
- ``main`` parses the vault path and runs the server on stdio.
"""

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
    """A folder of markdown notes, with every path checked against the root.

    All public methods take vault-relative paths and go through ``resolve``,
    so nothing outside ``root`` can be read or written through this class.
    """

    def __init__(self, root: Path):
        """Open a vault at ``root``.

        Args:
            root: Directory that holds the notes. Symlinks are resolved once
                here so later containment checks compare real paths.

        Raises:
            NotADirectoryError: If ``root`` does not exist or is not a directory.
        """
        self.root = root.resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)

    def resolve(self, rel: str) -> Path:
        """Turn a vault-relative path into an absolute one inside the root.

        Args:
            rel: Path relative to the vault root, for example ``projects/foo.md``.

        Returns:
            The absolute path with symlinks resolved.

        Raises:
            ValueError: If the resolved path is outside the root. This catches
                ``..`` segments, absolute paths, and symlinks that point out
                of the vault.
        """
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"path escapes vault root: {rel}")
        return target

    def notes(self, folder: str = ""):
        """Yield every ``.md`` file under ``folder``, walking subfolders.

        Skips any file with a dot-prefixed component in its vault-relative
        path (such as ``.obsidian/``), and any file whose real location is
        outside the root (a symlink pointing out of the vault).

        Args:
            folder: Vault-relative folder to start from. Empty means the root.

        Yields:
            Absolute ``Path`` objects, in filesystem walk order.

        Raises:
            ValueError: If ``folder`` resolves outside the root.
        """
        base = self.resolve(folder)
        for p in base.rglob("*.md"):
            rel = p.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not p.resolve().is_relative_to(self.root):
                continue
            yield p

    def rel(self, p: Path) -> str:
        """Return ``p`` as a vault-relative POSIX string.

        Args:
            p: Absolute path inside the root.

        Returns:
            Forward-slash path relative to the root, for example ``projects/foo.md``.

        Raises:
            ValueError: If ``p`` is not under the root.
        """
        return p.relative_to(self.root).as_posix()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Case-insensitive substring search over note bodies and filenames.

        Every note is read from disk on each call. There is no index.

        Scoring per note: one point per matching line, plus ten if the query
        appears in the filename stem. A note that matches only by filename
        contributes a single hit on line 1. All hits from a note share that
        note's score. Hits are sorted by score descending, then path, then
        line number.

        Args:
            query: Text to look for. Matching ignores case. Empty returns nothing.
            limit: Maximum number of hits to return.

        Returns:
            A list of dicts with keys ``file`` (vault-relative path), ``line``
            (1-based), ``excerpt`` (the matching line, stripped and cut to
            200 characters), and ``score``.
        """
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
        """Return the full text of one note.

        Args:
            rel: Vault-relative path of the note.

        Returns:
            The file contents decoded as UTF-8.

        Raises:
            ValueError: If ``rel`` resolves outside the root.
            FileNotFoundError: If the path does not exist or is not a regular file.
            UnicodeDecodeError: If the file is not valid UTF-8.
        """
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_text(encoding="utf-8")

    def list(self, folder: str = "", limit: int = 100) -> list[dict]:
        """List notes newest-modified first.

        Args:
            folder: Vault-relative folder to list. Empty means the whole vault.
            limit: Maximum number of entries to return.

        Returns:
            A list of dicts with keys ``path`` (vault-relative) and
            ``modified`` (local-time ISO 8601, second precision).

        Raises:
            ValueError: If ``folder`` resolves outside the root.
        """
        items = [(p.stat().st_mtime, p) for p in self.notes(folder)]
        items.sort(key=lambda t: -t[0])
        return [
            {"path": self.rel(p), "modified": dt.datetime.fromtimestamp(m).isoformat(timespec="seconds")}
            for m, p in items[:limit]
        ]

    def append_daily(self, text: str, now: dt.datetime | None = None) -> dict:
        """Append one timestamped bullet to the daily note for ``now``.

        The note lives at ``daily/YYYY-MM-DD.md``. If it does not exist it is
        created first from ``templates/daily.md`` with ``{{date}}`` replaced,
        or from a one-line ``# YYYY-MM-DD`` heading when there is no template.
        The appended line has the form ``- HH:MM <text>``. ``text`` is
        stripped of surrounding whitespace but not otherwise changed, so a
        newline inside it produces extra unprefixed lines.

        Args:
            text: The line to record.
            now: Timestamp to use. Defaults to the current local time.

        Returns:
            A dict with ``path`` (vault-relative), ``created`` (True if the
            file was made by this call), and ``line`` (what was appended,
            without the trailing newline).
        """
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
    """MCP server that exposes one ``Vault``.

    Registers four tools (``search_notes``, ``read_note``, ``list_notes``,
    ``append_daily_note``) and one resource template (``note://{+path}``).
    The SDK derives each tool's JSON schema from the method signature and
    its description from the docstring, so the docstrings below are what
    clients see.
    """

    def __init__(self, vault: Vault):
        """Wire ``vault`` to MCP tools and resources.

        Args:
            vault: The opened vault to serve.
        """
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
        """Resource handler for ``note://{+path}``.

        Args:
            path: The part of the URI after ``note://``, a vault-relative path.

        Returns:
            The note text.

        Raises:
            ResourceError: If the path is outside the vault or does not exist.
                The SDK turns this into a JSON-RPC error reply.
        """
        try:
            return self.vault.read(path)
        except (ValueError, FileNotFoundError) as e:
            raise ResourceError(str(e)) from e

    async def list_resources(self) -> list[Resource]:
        """Answer ``resources/list`` with one entry per note, sorted by path.

        Overrides the SDK default, which only knows about statically
        registered resources. The list is rebuilt from disk on every call.

        Returns:
            ``Resource`` objects with ``note://<path>`` URIs and
            ``text/markdown`` mime type.
        """
        return [
            Resource(uri=f"note://{self.vault.rel(p)}", name=self.vault.rel(p), mime_type="text/markdown")
            for p in sorted(self.vault.notes())
        ]


def main(argv=None):
    """Command line entry point.

    Args:
        argv: Argument list without the program name. ``None`` means
            ``sys.argv[1:]``. The single positional argument is the vault root.

    Raises:
        SystemExit: If the vault path is not a directory (exit status 1 with
            a message on stderr), or on argparse errors.
    """
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
