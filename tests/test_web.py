"""Dashboard server: serves the life folder, exposes Capri's state, and only accepts its own page."""
import http.client
import json

import pytest

from capri.web import TurnLog, WebServer


class FakeApp:
    def __init__(self):
        self.said, self.stopped = [], 0
        self.log = TurnLog()

    def web_state(self):
        return {"online": True, "state": "idle", "log": self.log.snapshot()}

    def web_say(self, text):
        self.said.append(text)

    def web_stop(self):
        self.stopped += 1

    def web_rebuild(self):
        return "rebuilt"


@pytest.fixture
def server(tmp_path):
    life = tmp_path / "life"
    (life / "dashboard").mkdir(parents=True)
    (life / "docs").mkdir()
    (life / ".claude").mkdir()
    (life / "dashboard" / "dashboard.html").write_text("<html>dash</html>")
    (life / "dashboard" / "canvas_feed.txt").write_text("https://secret")
    (life / "docs" / "My Syllabus.pdf").write_bytes(b"%PDF-1.4")
    (life / ".claude" / "settings.json").write_text("{}")
    (tmp_path / "outside.txt").write_text("nope")
    app = FakeApp()
    srv = WebServer(app, life, port=0)          # port 0 = any free port
    srv.port = srv.httpd.server_address[1]
    from capri.web import make_handler
    srv.httpd.RequestHandlerClass = make_handler(app, life, srv.port)
    srv.start()
    yield srv, app
    srv.stop()


def req(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    h = {"Host": f"127.0.0.1:{srv.port}"}
    h.update(headers or {})
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_root_redirects_to_dashboard_and_serves_it(server):
    srv, _ = server
    code, _, headers = req(srv, "GET", "/")
    assert code == 302 and headers["Location"] == "/life/dashboard/dashboard.html"
    code, body, _ = req(srv, "GET", "/life/dashboard/dashboard.html")
    assert code == 200 and b"dash" in body
    code, body, _ = req(srv, "GET", "/life/docs/My%20Syllabus.pdf")
    assert code == 200 and body.startswith(b"%PDF")


@pytest.mark.parametrize("path", ["/life/../outside.txt", "/life/%2e%2e/outside.txt", "/life/.claude/settings.json",
                                  "/life/dashboard/canvas_feed.txt", "/nope"])
def test_private_and_escaping_paths_are_hidden(server, path):
    srv, _ = server
    assert req(srv, "GET", path)[0] == 404


def test_state_say_stop(server):
    srv, app = server
    app.log.add("you", "open spotify")
    code, body, _ = req(srv, "GET", "/api/state")
    assert code == 200 and json.loads(body)["log"][0]["text"] == "open spotify"
    assert req(srv, "POST", "/api/say", {"text": "  what's   on today  "})[0] == 200
    assert app.said == ["what's on today"]
    assert req(srv, "POST", "/api/say", {"text": "   "})[0] == 400
    assert req(srv, "POST", "/api/stop", {})[0] == 200 and app.stopped == 1
    assert json.loads(req(srv, "POST", "/api/rebuild", {})[1])["result"] == "rebuilt"


def test_rejects_other_sites_and_dns_rebinding(server):
    srv, app = server
    # another website posting to Capri
    assert req(srv, "POST", "/api/say", {"text": "x"}, {"Origin": "https://evil.example"})[0] == 403
    # simple (no-preflight) cross-site form post
    assert req(srv, "POST", "/api/say", b"text=x", {"Content-Type": "text/plain"})[0] == 415
    # DNS rebinding: right IP, wrong Host
    assert req(srv, "GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403
    assert req(srv, "POST", "/api/say", {"text": "x"}, {"Host": "evil.example"})[0] == 403
    # own page is fine
    assert req(srv, "POST", "/api/say", {"text": "ok"}, {"Origin": f"http://127.0.0.1:{srv.port}"})[0] == 200
    assert app.said == ["ok"]


def test_turnlog_keeps_last_n():
    log = TurnLog(n=3)
    for i in range(5):
        log.add("you", f"t{i}")
    log.add("capri", "   ")
    assert [x["text"] for x in log.snapshot()] == ["t2", "t3", "t4"]
