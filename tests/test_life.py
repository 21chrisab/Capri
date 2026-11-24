"""Life OS: the life/ folder Capri reads and writes (inbox, today, projects, dashboard)."""
import datetime as dt
import sys
import types as pytypes

import pytest

sys.modules.setdefault("sounddevice", pytypes.ModuleType("sounddevice"))

from capri.life import Life, _speakable, find  # noqa: E402
from capri.router import Intent, parse  # noqa: E402

TODAY = dt.date(2026, 11, 13)

TODAY_MD = """# Today: 2026-11-13

## Top 3
- [ ] Run Capri v2 on the PC (`capri.bat doctor`, then `capri.bat`)
- [x] Fill in ME.md
- [ ] Finish the Brilliant-style site

## Also
- [ ] Connect Google Calendar

## Calendar
- 10:00-11:00 EE 319K
"""

CAPRI_MD = """---
status: active
area: work
next: Run capri.bat doctor
---
# Capri

## Tasks
- [ ] Pick a far-field mic under $50 due:2026-11-15
- [ ] M5 learned shortcuts
- [x] M4 hands

## Notes
- lean rebuild
"""

SKATE_MD = """---
status: active
area: work
next: Finish the Brilliant-style site
---
# Skatebored

## Tasks
- [ ] Finish the Brilliant-style site
- [ ] Sana style version
"""


@pytest.fixture
def life(tmp_path):
    root = tmp_path / "life"
    (root / "projects").mkdir(parents=True)
    (root / "today.md").write_text(TODAY_MD)
    (root / "inbox.md").write_text("# Inbox\n\n<!-- dump things here -->\n")
    (root / "projects" / "capri.md").write_text(CAPRI_MD)
    (root / "projects" / "skatebored.md").write_text(SKATE_MD)
    return Life(root)


def test_find_uses_config_dir(life, tmp_path):
    assert find({"life": {"dir": str(life.root)}}).root == life.root
    assert find({"life": {"dir": str(tmp_path / "nope")}}) is None


def test_today_summary_speaks_open_top3(life):
    s = life.today_summary(today=TODAY)
    assert s.startswith("Top 2 today: Run Capri v2 on the PC, and Finish the Brilliant-style site.")
    assert "`" not in s and "(" not in s
    assert "1 thing due in the next few days" in s
    assert "Heads up" not in s


def test_today_summary_flags_old_list(life):
    assert "that list is from Friday" in life.today_summary(today=TODAY + dt.timedelta(days=1))


def test_capture_appends_to_inbox(life):
    assert life.capture("  buy an HDMI cable. ") == "Added to your inbox: buy an HDMI cable"
    assert (life.root / "inbox.md").read_text().endswith("<!-- dump things here -->\n- buy an HDMI cable\n")


def test_done_checks_off_everywhere_it_appears(life):
    assert life.done("brilliant site") == "Checked off: finish the brilliant-style site"
    assert "- [x] Finish the Brilliant-style site" in (life.root / "today.md").read_text()
    assert "- [x] Finish the Brilliant-style site" in (life.root / "projects/skatebored.md").read_text()
    assert "- [ ] Sana style version" in (life.root / "projects/skatebored.md").read_text()


def test_done_asks_when_ambiguous_and_reports_misses(life):
    life.add_task("Finish the Sana site", "skatebored")
    assert life.done("finish site").startswith("Which one:")
    assert life.done("walk the dog").startswith("Couldn't find")
    assert life.done("the") == "Which task?"


def test_add_task_goes_into_project_tasks_section(life):
    assert life.add_task("Order the mic", "capri", "2026-11-20") == "Added to capri: Order the mic"
    text = (life.root / "projects/capri.md").read_text()
    tasks = text.split("## Tasks")[1].split("## Notes")[0]
    assert "- [ ] Order the mic due:2026-11-20" in tasks
    assert life.add_task("Something", "no such project").startswith("Added to your inbox")


def test_projects_text_and_project_lookup(life):
    t = life.projects_text()
    assert "- capri [active, work] next: Run capri.bat doctor (2 open tasks)" in t
    assert "Skatebored" in life.project_text("skatebored")
    assert "no project matches" in life.project_text("zzz")


def test_dashboard_build_errors_are_reported(life):
    assert "missing" in life.dashboard(open_it=False)
    (life.root / "dashboard").mkdir()
    (life.root / "dashboard" / "build.py").write_text("raise SystemExit('boom')")
    assert "failed" in life.dashboard(open_it=False)
    (life.root / "dashboard" / "build.py").write_text(
        "import pathlib; pathlib.Path(__file__).with_name('dashboard.html').write_text('ok')")
    assert life.dashboard(open_it=False) == "opened the dashboard"


def test_speakable():
    assert _speakable("Run it (`capri.bat`) due:2026-11-20") == "Run it"


@pytest.mark.parametrize("text,want", [
    ("Open my dashboard", Intent("life_dashboard")),
    ("show me the dashboard", Intent("life_dashboard")),
    ("What's on my plate today?", Intent("life_today")),
    ("what's on today", Intent("life_today")),
    ("What do I have to do today?", Intent("life_today")),
    ("Add buy an HDMI cable to my inbox", Intent("life_capture", "buy an hdmi cable")),
    ("put call the advisor on my to do list", Intent("life_capture", "call the advisor")),
    ("Note to self, email professor Lee", Intent("life_capture", "email professor lee")),
    ("Mark the mic task done", Intent("life_done", "the mic task")),
    ("check off brilliant site", Intent("life_done", "brilliant site")),
    ("I finished the degree plan", Intent("life_done", "the degree plan")),
])
def test_router_life_intents(text, want):
    assert parse(text) == want


def test_router_still_opens_apps():
    assert parse("open discord") == Intent("app_open", "discord")


def test_toolbox_life_tools(life):
    from capri.memory import Memory
    from capri.tools import ToolBox
    tb = ToolBox(Memory(life.root.parent / "mem", life.root.parent / "w.jsonl"), search=lambda q: "", life=life)
    assert "Top 3" in tb.run("life_today", {})
    assert tb.run("life_add", {"text": "renew passport"}).startswith("Added to your inbox")
    assert tb.run("life_done", {"task": "renew passport"}).startswith("Couldn't find")  # inbox lines aren't tasks
    off = ToolBox(Memory(life.root.parent / "mem2", life.root.parent / "w2.jsonl"), search=lambda q: "")
    assert "isn't set up" in off.run("life_today", {})


def test_canvas_items_count_as_due_soon(life):
    (life.root / "canvas.md").write_text(
        "# Canvas\nsynced: x\n\n## Upcoming\n- [ ] Lab 7 [EE 319K] due:2026-11-14 23:59\n"
        "- [ ] Old HW [EE 319K] due:2026-11-01 23:59\n")
    assert "2 things due in the next few days" in life.today_summary(today=TODAY)   # mic task + Lab 7
    assert "Lab 7" in life.today_text()
