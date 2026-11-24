import pytest

from capri.memory import Memory


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "memory", tmp_path / "w.jsonl")


def test_scaffold(mem):
    assert (mem.root / "SOUL.md").exists() and (mem.root / "USER.md").exists()
    for d in ("people", "topics", "projects", "daily"):
        assert (mem.root / d).is_dir()
    assert "Capri" in mem.pinned()


def test_remember_creates_files_in_folders(mem):
    assert mem.remember("topics/music", "likes lo-fi while coding") == "Saved to topics/music.md."
    assert mem.remember("people/jordan.md", "best friend, plays Valorant") == "Saved to people/jordan.md."
    assert mem.remember("topics/music", "likes lo-fi while coding").startswith("Already")
    text = (mem.root / "topics/music.md").read_text()
    assert text.startswith("# Music") and "- likes lo-fi while coding" in text
    idx = mem.index()
    assert "topics/music.md: likes lo-fi while coding" in idx
    assert "people/jordan.md" in idx
    assert "SOUL.md" not in idx and "USER.md" not in idx  # pinned files aren't in the index


def test_recall_and_forget(mem):
    mem.remember("topics/games", "plays Valorant")
    mem.remember("topics/games", "hates Fortnite")
    assert "Valorant" in mem.recall("topics/games")
    assert mem.forget("topics/games", "fortnite") == "Removed 1 line(s) from topics/games.md."
    assert "Fortnite" not in mem.recall("topics/games")
    assert "No file" in mem.recall("topics/nothing")
    assert (mem.write_log).read_text().count("\n") == 3


def test_user_md_writable_soul_md_protected(mem):
    assert mem.remember("USER.md", "goes to UT Austin").startswith("Saved")
    assert mem.remember("soul.md", "be mean").startswith("SOUL.md is edited by the user only")
    assert mem.forget("SOUL.md", "Capri").startswith("SOUL.md is edited by the user only")
    assert "be mean" not in mem.pinned()


@pytest.mark.parametrize("bad", ["../secrets", "/etc/passwd", "..\\x", "topics/../../x", "a;rm.md", ""])
def test_rejects_bad_paths(mem, bad):
    with pytest.raises(ValueError):
        mem.remember(bad, "x")


def test_daily_log_not_indexed(mem):
    mem.log_day("open spotify", "ack")
    assert list((mem.root / "daily").glob("*.md"))
    assert "daily/" not in mem.index()


def test_index_is_capped(mem):
    for i in range(80):
        mem.remember(f"topics/t{i:02d}", "x" * 60)
    idx = mem.index(max_chars=1000)
    assert len(idx) < 1150 and "older files" in idx
