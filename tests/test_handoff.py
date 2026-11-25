import json
import subprocess
from pathlib import Path

from capri.handoff import Handoff, build_command, explain_error, parse_output, slug

REAL_ERROR = json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                         "result": "Authentication error · This may be a temporary network issue, please try again",
                         "session_id": "f3f3", "total_cost_usd": 0, "terminal_reason": "api_error"})


def test_command_is_sandboxed():
    cmd = build_command("claude", 40, "sonnet")
    assert cmd[:3] == ["claude", "-p", cmd[2]] and "TASK.md" in cmd[2]
    i = cmd.index
    assert cmd[i("--permission-mode") + 1] == "acceptEdits"
    assert "Bash" in cmd[i("--disallowedTools") + 1]
    assert cmd[i("--output-format") + 1] == "json" and cmd[i("--model") + 1] == "sonnet"
    assert "--model" not in build_command("claude", 40)


def test_parse_success_and_real_error_shape():
    ok = parse_output("noise\n" + json.dumps({"type": "result", "is_error": False, "result": "Done. Top pick is the Fifine K688.",
                                               "session_id": "abc", "total_cost_usd": 0.12, "num_turns": 7}))
    assert ok == {"ok": True, "summary": "Done. Top pick is the Fifine K688.", "session_id": "abc", "cost": 0.12, "turns": 7}
    bad = parse_output(REAL_ERROR)                       # captured from a real `claude -p` run
    assert bad["ok"] is False and "Authentication" in bad["summary"]
    assert "isn't logged in" in explain_error(bad["summary"])
    assert parse_output("not json at all")["ok"] is False


def test_slug():
    assert slug("Research: best far-field mics under $50!") == "research-best-far-field-mics-under-50"


class FakeRun:
    def __init__(self, stdout, write_result=True):
        self.stdout, self.write_result, self.calls = stdout, write_result, []

    def __call__(self, cmd, cwd, **kw):
        self.calls.append((cmd, cwd))
        if self.write_result:
            Path(cwd, "RESULT.md").write_text("# Mics\n1. Fifine K688\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


def wait(done):
    import time
    for _ in range(200):
        if done:
            return
        time.sleep(0.01)


def test_job_lifecycle(tmp_path):
    done = []
    run = FakeRun(json.dumps({"is_error": False, "result": "Found three good mics; the K688 is the best value.",
                              "session_id": "s1"}))
    h = Handoff({}, on_done=done.append, runner=run, jobs_dir=tmp_path)
    msg = h.start("Compare far-field USB mics under $50 for a voice assistant.", "Mic comparison")
    assert msg.startswith("Started Claude job #1")
    wait(done)
    job = done[0]
    assert job.status == "done" and "K688" in job.summary
    assert (job.folder / "TASK.md").read_text().startswith("# Mic comparison")
    assert run.calls[0][1] == str(job.folder)            # Claude runs inside the job folder
    assert "Fifine K688" in h.result()
    assert "#1 Mic comparison: done" in h.status()


def test_failure_is_spoken_plainly(tmp_path):
    done = []
    h = Handoff({}, on_done=done.append, runner=FakeRun(REAL_ERROR, write_result=False), jobs_dir=tmp_path)
    h.start("anything")
    wait(done)
    assert done[0].status == "failed" and "isn't logged in" in done[0].summary


def test_parallel_limit(tmp_path):
    import threading
    gate = threading.Event()

    def slow(cmd, cwd, **kw):
        gate.wait(2)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
    h = Handoff({"handoff": {"max_parallel": 1}}, runner=slow, jobs_dir=tmp_path)
    h.start("one")
    assert "already busy" in h.start("two")
    gate.set()


def test_codex_and_gemini_commands_stay_sandboxed():
    from capri.handoff import build_agent_command
    codex = build_agent_command("codex", "codex", 40, "gpt-5")
    assert codex[:2] == ["codex", "exec"] and codex[codex.index("--sandbox") + 1] == "workspace-write"
    assert "--skip-git-repo-check" in codex and codex[-2:] == ["-m", "gpt-5"]
    gem = build_agent_command("gemini", "gemini", 40)
    assert gem[gem.index("--approval-mode") + 1] == "auto_edit" and "-m" not in gem
    for cmd in (codex, gem):                       # never the "approve everything" switches
        assert not {"--yolo", "-y", "--full-auto", "danger-full-access", "--dangerously-bypass-approvals-and-sandbox"} & set(cmd)
    assert build_agent_command("claude", "claude", 40) == build_command("claude", 40)


class FakeCLI:
    def __init__(self, stdout, code=0):
        self.stdout, self.code, self.calls = stdout, code, []

    def __call__(self, cmd, cwd, **kw):
        self.calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.stdout, stderr="boom" if self.code else "")


def test_codex_job_and_task_context(tmp_path):
    from capri.memory import Memory
    mem = Memory(tmp_path / "mem", tmp_path / "w.jsonl")
    mem.set_mode("coding")
    mem.learn_lesson("Never commit to main directly")
    done = []
    run = FakeCLI("working...\n\nAdded the parser and three tests.")
    h = Handoff({}, on_done=done.append, runner=run, jobs_dir=tmp_path / "jobs", memory=mem)
    assert h.start("Write a CSV parser", "CSV parser", agent="Codex").startswith("Started Codex job #1")
    wait(done)
    job = done[0]
    assert job.agent == "codex" and job.status == "done"
    assert job.summary == "Added the parser and three tests."
    assert run.calls[0][0][1] == "exec"
    task = (job.folder / "TASK.md").read_text()
    assert "Workflow mode: coding" in task and "Never commit to main directly" in task
    assert "RESULT.md" in task                          # non-Claude agents get the finishing rules in TASK.md
    assert "(Codex)" in h.status()


def test_gemini_failure_and_bad_agent(tmp_path):
    done = []
    h = Handoff({"handoff": {"default_agent": "gemini"}}, on_done=done.append,
                runner=FakeCLI("", code=1), jobs_dir=tmp_path)
    assert h.start("Summarize this").startswith("Started Gemini job")
    wait(done)
    assert done[0].status == "failed" and done[0].summary.startswith("Gemini hit a problem: boom")
    assert h.start("x", agent="grok").startswith("Unknown agent")
