import datetime as dt
import os
import sys
from pathlib import Path
import time

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from server import Vault, VaultServer, main

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "alpha.md").write_text("# Alpha\n\nfirst note about Cats\n")
    (tmp_path / "projects" / "beta.md").write_text("# Beta\n\ncats and dogs\nmore cats\n")
    (tmp_path / "gamma.md").write_text("# Gamma\n\nnothing here\n")
    (tmp_path / ".obsidian" / "hidden.md").write_text("cats in config\n")
    (tmp_path / "outside.txt").write_text("cats outside\n")
    (tmp_path.parent / "secret.md").write_text("cats secret\n")
    old = time.time() - 1000
    os.utime(tmp_path / "alpha.md", (old, old))
    return Vault(tmp_path)


def test_search_ranks_and_skips_hidden(vault):
    hits = vault.search("cats")
    files = [h["file"] for h in hits]
    assert files == ["projects/beta.md", "projects/beta.md", "alpha.md"]
    assert hits[0]["line"] == 3 and hits[0]["excerpt"] == "cats and dogs"
    assert vault.search("Cats", limit=1) == hits[:1]
    assert vault.search("") == []
    assert vault.search("zzz") == []


def test_search_filename_match(vault):
    assert vault.search("gamma")[0]["file"] == "gamma.md"


def test_read_note(vault):
    assert vault.read("projects/beta.md").startswith("# Beta")
    with pytest.raises(FileNotFoundError):
        vault.read("missing.md")


def test_list_notes_mtime_order_and_folder(vault):
    assert [n["path"] for n in vault.list()][-1] == "alpha.md"
    assert [n["path"] for n in vault.list("projects")] == ["projects/beta.md"]
    assert len(vault.list(limit=1)) == 1


@pytest.mark.parametrize("bad", ["../secret.md", "projects/../../secret.md", "/etc/passwd"])
def test_path_escape_refused(vault, bad):
    with pytest.raises(ValueError):
        vault.read(bad)
    with pytest.raises(ValueError):
        vault.list(bad)


def test_symlink_escape_refused(vault):
    (vault.root / "link.md").symlink_to(vault.root.parent / "secret.md")
    with pytest.raises(ValueError):
        vault.read("link.md")


def test_symlink_escape_hidden_from_search_and_list(vault):
    # Regression: a symlink to a file outside the vault used to be listed and
    # searched (leaking its text) even though read() refused it.
    (vault.root / "link.md").symlink_to(vault.root.parent / "secret.md")
    assert "link.md" not in [n["path"] for n in vault.list()]
    assert "link.md" not in [h["file"] for h in vault.search("secret")]
    # A symlink whose target is inside the vault is still fine.
    (vault.root / "inner.md").symlink_to(vault.root / "alpha.md")
    assert "inner.md" in [n["path"] for n in vault.list()]


def test_append_daily_creates_then_appends(vault):
    now = dt.datetime(2026, 9, 10, 10, 41)
    r1 = vault.append_daily("hello", now)
    assert r1 == {"path": "daily/2026-09-10.md", "created": True, "line": "- 10:41 hello"}
    r2 = vault.append_daily("again", now.replace(minute=42))
    assert r2["created"] is False
    assert vault.read("daily/2026-09-10.md") == "# 2026-09-10\n\n- 10:41 hello\n- 10:42 again\n"
    assert vault.list("daily")[0]["path"] == "daily/2026-09-10.md"


def test_append_daily_uses_template(vault):
    (vault.root / "templates").mkdir()
    (vault.root / "templates" / "daily.md").write_text("# Log {{date}}\n\n## Notes\n")
    vault.append_daily("x", dt.datetime(2026, 1, 2, 9, 0))
    assert vault.read("daily/2026-01-02.md") == "# Log 2026-01-02\n\n## Notes\n- 09:00 x\n"


def test_main_rejects_missing_dir(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope")])


async def test_stdio_end_to_end(vault):
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).parent.parent / "server.py"), str(vault.root)])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert names == {"search_notes", "read_note", "list_notes", "append_daily_note"}

        res = await s.call_tool("search_notes", {"query": "cats", "limit": 1})
        assert res.structured_content["result"][0]["file"] == "projects/beta.md"

        res = await s.call_tool("read_note", {"path": "alpha.md"})
        assert res.content[0].text.startswith("# Alpha")

        res = await s.call_tool("list_notes", {"folder": "projects"})
        assert res.structured_content["result"][0]["path"] == "projects/beta.md"

        res = await s.call_tool("read_note", {"path": "../secret.md"})
        assert res.is_error and "escapes" in res.content[0].text

        res = await s.call_tool("append_daily_note", {"text": "wire"})
        assert res.structured_content["created"] is True
        assert (vault.root / "daily" / f"{dt.date.today():%Y-%m-%d}.md").exists()

        uris = {str(x.uri) for x in (await s.list_resources()).resources}
        assert uris == {"note://alpha.md", "note://gamma.md", "note://projects/beta.md",
                        f"note://daily/{dt.date.today():%Y-%m-%d}.md"}
        rr = await s.read_resource("note://projects/beta.md")
        assert rr.contents[0].text.startswith("# Beta")
