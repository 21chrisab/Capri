"""Brain turn logic against a fake Gemini that streams real google-genai types."""
import queue
import sys
import types as pytypes

import pytest

sys.modules.setdefault("sounddevice", pytypes.ModuleType("sounddevice"))

from google.genai import errors  # noqa: E402
from google.genai import types as t  # noqa: E402

from capri.brain import Brain  # noqa: E402
from capri.gen import Generation  # noqa: E402
from capri.memory import Memory  # noqa: E402
from capri.timing import TurnTimer  # noqa: E402
from capri.voice import Utterance  # noqa: E402


def chunk(*parts):
    return t.GenerateContentResponse(candidates=[t.Candidate(content=t.Content(role="model", parts=list(parts)))])


class FakeModels:
    def __init__(self, scripts, missing=()):
        self.scripts, self.missing, self.calls = list(scripts), set(missing), []

    def generate_content_stream(self, model, contents, config):
        self.calls.append((model, contents, config))
        if model in self.missing:
            raise errors.ClientError(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
        return iter(self.scripts.pop(0))

    def generate_content(self, model, contents, config):
        return pytypes.SimpleNamespace(text="It's 72 and sunny in Austin.")


class FakeVoice:
    def __init__(self, gen):
        import threading
        self.gen, self._q, self.acks, self.said = gen, queue.Queue(), [], []

        def player():  # stands in for the speaker: "plays" instantly
            while True:
                _, _, started = self._q.get()
                if started is not None:
                    started.set()
        threading.Thread(target=player, daemon=True).start()

    def render(self, s):
        self.said.append(s)
        return s

    def utterance(self, started=None):
        return Utterance(self, started)

    def ack(self, text, started=None):
        self.acks.append(text)
        if started:
            started.set()

    def say(self, text, started=None):
        self.said.append(text)


@pytest.fixture
def setup(tmp_path):
    gen = Generation()
    voice = FakeVoice(gen)
    mem = Memory(tmp_path / "memory", tmp_path / "w.jsonl")

    def make(scripts, missing=()):
        models = FakeModels(scripts, missing)
        b = Brain(voice, gen, mem, client=pytypes.SimpleNamespace(models=models),
                  models=["gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview"])
        return b, models
    return make, voice, mem, tmp_path


def wait_said(voice, n, timeout=2):
    import time
    end = time.time() + timeout
    while len(voice.said) < n and time.time() < end:
        time.sleep(0.01)


def test_streams_sentences_in_order(setup):
    make, voice, _, tmp = setup
    b, _ = make([[chunk(t.Part(text="Honestly? ")), chunk(t.Part(text="Lo-fi is a vibe. Want me ")),
                  chunk(t.Part(text="to put some on?"))]])
    timer = TurnTimer("brain", tmp / "t.jsonl")
    reply = b.think("what music should I code to", timer)
    wait_said(voice, 3)
    # short first sentence goes out alone: that's what makes first audio fast
    assert voice.said == ["Honestly?", "Lo-fi is a vibe.", "Want me to put some on?"]
    assert "Lo-fi is a vibe" in reply and "llm_first_token" in timer.marks


def test_tool_call_then_answer_with_memory(setup):
    make, voice, mem, tmp = setup
    call = t.Part(function_call=t.FunctionCall(name="remember",
                                               args={"path": "people/jordan.md", "fact": "best friend, plays Valorant"}))
    b, models = make([[chunk(call)], [chunk(t.Part(text="Got it, noted."))]])
    b.think("my best friend Jordan plays valorant", TurnTimer("brain", tmp / "t.jsonl"))
    assert "best friend, plays Valorant" in mem.recall("people/jordan.md")
    # second request carried the function response back to the model
    second = models.calls[1][1]  # [user, model(call), user(response), ...]
    assert second[2].parts[0].function_response.response == {"result": "Saved to people/jordan.md."}
    assert voice.acks == []  # memory isn't an action tool, so no "On it." ack
    # the memory index in the next prompt shows the new file
    assert "people/jordan.md" in b.system_prompt()


def test_action_tool_acks_once_and_falls_back_to_next_model(setup):
    make, voice, _, tmp = setup
    b, models = make([[chunk(t.Part(function_call=t.FunctionCall(name="web_search", args={"query": "austin weather"})))],
                      [chunk(t.Part(text="It's 72 and sunny."))]], missing={"gemini-3.1-flash-lite"})
    b.think("weather?", TurnTimer("brain", tmp / "t.jsonl"))
    assert models.calls[0][0] == "gemini-3.1-flash-lite"
    assert all(c[0] == "gemini-3.1-flash-lite-preview" for c in models.calls[1:])
    assert b._model_ok == "gemini-3.1-flash-lite-preview"


def test_history_and_prompt_cap(setup):
    make, voice, mem, tmp = setup
    for i in range(100):
        mem.remember(f"topics/t{i:03d}", "y" * 80)
    b, _ = make([[chunk(t.Part(text="Sure."))], [chunk(t.Part(text="Yep."))]])
    assert len(b.system_prompt()) <= 6200
    b.think("first", TurnTimer("brain", tmp / "t.jsonl"))
    b.think("second", TurnTimer("brain", tmp / "t.jsonl"))
    assert [c.parts[0].text for c in b.history] == ["first", "Sure.", "second", "Yep."]
    assert list((mem.root / "daily").glob("*.md"))


def test_no_key_says_so(setup, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _, voice, mem, tmp = setup
    b = Brain(voice, Generation(), mem)
    assert not b.ready
    assert b.think("hi", TurnTimer("brain", tmp / "t.jsonl")) == "no key"
    assert "Gemini key" in voice.said[-1]


def test_env_loader(tmp_path, monkeypatch):
    from capri.env import load_env
    monkeypatch.delenv("CAPRI_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text('# c\nCAPRI_TEST_KEY="abc 123"  \nBAD LINE\nCAPRI_X=1 # note\n')
    load_env(tmp_path / ".env")
    import os
    assert os.environ["CAPRI_TEST_KEY"] == "abc 123" and os.environ["CAPRI_X"] == "1"


def test_computer_task_observe_then_click(setup):
    make, voice, mem, tmp = setup

    class FakeHands:
        vision = "unset"

        def __init__(self):
            self.calls = []

        def observe(self, window=None):
            self.calls.append(("observe", window))
            return 'Window: "Spotify" (Spotify.exe) on monitor 2.\n[1] Button "Home"\n[2] ListItem "Liked Songs"'

        def click(self, id, confirmed=False):
            self.calls.append(("click", id))
            return f'clicked [{id}] ListItem "Liked Songs"; the screen changed'

    hands = FakeHands()
    models = FakeModels([
        [chunk(t.Part(function_call=t.FunctionCall(name="observe", args={"window": "spotify"})))],
        [chunk(t.Part(function_call=t.FunctionCall(name="click", args={"id": "2"})))],
        [chunk(t.Part(text=""))],
    ])
    b = Brain(voice, Generation(), mem, client=pytypes.SimpleNamespace(models=models),
              models=["gemini-3.1-flash-lite"], hands=hands)
    shown = []
    b.tools.on_hands = lambda: shown.append("computer")
    timer = TurnTimer("brain", tmp / "t.jsonl")
    b.think("open my liked songs on spotify", timer)
    assert hands.calls == [("observe", "spotify"), ("click", "2")]
    assert voice.acks == ["On it."]                 # one ack, not one per tool
    assert shown == ["computer", "computer"] and timer.kind == "computer"
    # the snapshot text went back to the model as the tool result
    assert "Liked Songs" in models.calls[1][1][2].parts[0].function_response.response["result"]


def test_hand_tools_without_hands(setup):
    make, voice, mem, tmp = setup
    from capri.tools import ToolBox
    tb = ToolBox(mem, lambda q: "", hands=None)
    assert tb.run("observe", {}) == "screen control isn't available"
