import pytest
from PIL import Image, ImageDraw

from capri.hands import (Element, changed, format_snapshot, is_sensitive, mean_hash, monitor_of,
                         needs_confirm, ocr_elements, parse_keys, pick_elements)


def item(name, typ="ButtonControl", rect=(0, 0, 100, 30), **kw):
    return {"name": name, "type": typ, "rect": rect, "enabled": True, "offscreen": False, **kw}


def test_pick_elements_filters_and_ranks():
    raw = [
        item("Liked Songs", "ListItemControl", (10, 300, 200, 330)),
        item("Home", "ButtonControl", (10, 50, 80, 80)),
        item("", "ButtonControl", (90, 50, 120, 80)),                    # unnamed button: dropped
        item("", "EditControl", (200, 50, 600, 80), value="lofi"),       # unnamed edit: kept
        item("Hidden", "ButtonControl", offscreen=True),                 # offscreen: dropped
        item("Disabled", "ButtonControl", enabled=False),                # disabled: dropped
        item("Tiny", "ButtonControl", (0, 0, 2, 2)),                     # zero-size: dropped
        item("Pane", "PaneControl", (0, 0, 900, 900)),                   # container: dropped
        item("Daft Punk - One More Time", "TextControl", (300, 900, 700, 920)),
        item("Home", "ButtonControl", (10, 50, 80, 80)),                 # duplicate: dropped
    ]
    els = pick_elements(raw)
    assert [(e.type, e.name) for e in els] == [
        ("Button", "Home"), ("Edit", ""), ("ListItem", "Liked Songs"), ("Text", "Daft Punk - One More Time")]
    assert [e.id for e in els] == ["1", "2", "3", "4"]
    assert els[1].value == "lofi"


def test_pick_elements_caps_size():
    raw = [item(f"Row {i}", "ListItemControl", (0, i * 30, 300, i * 30 + 28)) for i in range(500)]
    raw += [item(f"label {i}", "TextControl", (400, i * 30, 600, i * 30 + 20)) for i in range(100)]
    els = pick_elements(raw)
    assert len(els) == 90 + 25


def test_snapshot_format():
    els = [Element("1", "uia", "Edit", "Search", (0, 0, 10, 10), value="lofi", focused=True),
           Element("2", "uia", "Button", "Play", (0, 0, 10, 10))]
    s = format_snapshot("Spotify Premium", "Spotify.exe", 2, els, "UI Automation")
    assert 'Window: "Spotify Premium" (Spotify.exe) on monitor 2' in s
    assert "[1] Edit \"Search\" (value='lofi', focused)" in s
    assert '[2] Button "Play"' in s
    assert "read_screen" in format_snapshot("x", "y.exe", 1, [], "UI Automation")


def test_ocr_elements_offsets_and_ids():
    lines = [{"text": "Liked Songs", "words": [
        {"text": "Liked", "bounding_rect": {"x": 10, "y": 20, "width": 40, "height": 12}},
        {"text": "Songs", "bounding_rect": {"x": 55, "y": 21, "width": 45, "height": 12}}]},
        {"text": "", "words": []}]
    els = ocr_elements(lines, offset=(1920, 0), start=3)
    assert len(els) == 1
    e = els[0]
    assert e.id == "t3" and e.kind == "ocr" and e.rect == (1930, 20, 2020, 33)
    assert e.center == (1975, 26)


def test_monitor_of():
    mons = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
    assert monitor_of((100, 100, 500, 500), mons) == 1
    assert monitor_of((2000, 100, 2400, 500), mons) == 2
    assert monitor_of((-5000, 0, -4000, 10), mons) == 1


@pytest.mark.parametrize("label", ["Password", "Enter PIN", "CVV", "Card number", "Security code",
                                   "One-time code", "Social Security Number"])
def test_sensitive_fields(label):
    assert is_sensitive(label)


@pytest.mark.parametrize("label", ["Search", "Message #general", "Spinning", "Opinion", "Username"])
def test_not_sensitive(label):
    assert not is_sensitive(label)


@pytest.mark.parametrize("label,want", [("Send", True), ("Delete chat", True), ("Place order", True),
                                        ("Buy now", True), ("Play", False), ("Search", False),
                                        ("Sender name", False), ("Liked Songs", False)])
def test_needs_confirm(label, want):
    assert needs_confirm(label) is want


def test_hash_detects_change():
    a = Image.new("RGB", (400, 300), "white")
    b = a.copy()
    ImageDraw.Draw(b).rectangle((0, 0, 400, 150), fill="black")
    assert not changed(mean_hash(a), mean_hash(a.copy()))
    assert changed(mean_hash(a), mean_hash(b))


def test_parse_keys():
    assert parse_keys("ctrl+shift+t") == [0x11, 0x10, ord("T")]
    assert parse_keys("Alt + F4") == [0x12, 0x73]
    assert parse_keys("enter") == [0x0D]
    with pytest.raises(ValueError):
        parse_keys("ctrl+banana")
