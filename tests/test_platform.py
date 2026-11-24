"""Tests for platform switching, Brain hands=None, config.local.toml, and [memory] dir."""
import importlib
import sys
import types

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

from capri import config as config_mod
from capri import memory as memory_mod
from capri import voice as voice_mod
from capri.brain import PROMPT_CAP, Brain
from capri.gen import Generation
from capri.memory import Memory, memory_dir
from capri.tools import HAND_TOOLS


def test_sysctl_and_sysapps_platform_switch(monkeypatch):
    def fresh(name):
        sys.modules.pop(name, None)            # reload() would keep names left over from the other platform
        return importlib.import_module(name)

    monkeypatch.setattr(sys, "platform", "darwin")
    sysctl, sysapps = fresh("capri.sysctl"), fresh("capri.sysapps")
    assert sysctl.find_window.__module__ == "capri.macctl"
    assert sysapps.match.__module__ == "capri.macapps"
    assert not hasattr(sysctl, "tap")

    monkeypatch.setattr(sys, "platform", "win32")
    sysctl, sysapps = fresh("capri.sysctl"), fresh("capri.sysapps")
    assert sysctl.find_window.__module__ == "capri.winctl"
    assert sysapps.match.__module__ == "capri.apps"


def test_brain_omits_hand_tools_and_adds_rule_when_hands_none(tmp_path):
    gen = Generation()
    mem = Memory(tmp_path / "memory", tmp_path / "w.jsonl")
    dummy_voice = types.SimpleNamespace()

    b_no_hands = Brain(dummy_voice, gen, mem, client=types.SimpleNamespace(), hands=None)
    prompt_no_hands = b_no_hands.system_prompt()
    assert "You can't see or click inside apps on this computer yet." in prompt_no_hands
    assert len(prompt_no_hands) <= PROMPT_CAP + 200
    cfg_no_hands = b_no_hands._config("gemini-3.1-flash-lite")
    names_no_hands = {d.name for d in cfg_no_hands.tools[0].function_declarations}
    assert not (names_no_hands & HAND_TOOLS)
    assert "open_app" in names_no_hands

    b_with_hands = Brain(dummy_voice, gen, mem, client=types.SimpleNamespace(),
                         hands=types.SimpleNamespace(vision=None))
    prompt_with_hands = b_with_hands.system_prompt()
    assert "You can't see or click inside apps on this computer yet." not in prompt_with_hands
    cfg_with_hands = b_with_hands._config("gemini-3.1-flash-lite")
    names_with_hands = {d.name for d in cfg_with_hands.tools[0].function_declarations}
    assert HAND_TOOLS <= names_with_hands


def test_config_local_toml_merge(tmp_path):
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text('[voice]\nvoice = "af_bella"\nmodel_dir = "models"\n[listen]\nmode = "both"\n',
                    encoding="utf-8")
    local.write_text('[voice]\nmodel_dir = "~/.capri/models"\nprovider = "coreml"\n', encoding="utf-8")

    cfg = config_mod.load(base)
    assert cfg["voice"]["voice"] == "af_bella"
    assert cfg["voice"]["model_dir"] == "~/.capri/models"
    assert cfg["voice"]["provider"] == "coreml"
    assert cfg["listen"]["mode"] == "both"


def test_memory_dir_and_model_dir_expansion(tmp_path):
    assert memory_dir({}) == memory_mod.MEM
    assert memory_dir({"memory": {"dir": ""}}) == memory_mod.MEM
    assert memory_dir({"memory": {"dir": "~/Drive/CapriMemory"}}) == (tmp_path.home() / "Drive/CapriMemory")
    abs_dir = str(tmp_path / "shared_mem")
    assert memory_dir({"memory": {"dir": abs_dir}}) == tmp_path / "shared_mem"

    assert voice_mod._model_dir({"voice": {"model_dir": "~/.capri/models"}}) == tmp_path.home() / ".capri/models"


def test_zz_restore_real_platform_modules():
    for name in ("capri.sysctl", "capri.sysapps"):
        sys.modules.pop(name, None)
        importlib.import_module(name)
