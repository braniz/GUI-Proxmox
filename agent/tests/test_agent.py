import json
import os
import stat
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

import pytest

_path = os.path.join(os.path.dirname(__file__), "..", "prox-agent")
_spec = spec_from_loader("prox_agent", SourceFileLoader("prox_agent", _path))
agent = module_from_spec(_spec)
_spec.loader.exec_module(agent)


def test_config_defaults_and_parsing(tmp_path):
    assert agent.load_config(str(tmp_path / "missing"))["interval"] == 60
    conf = tmp_path / "c.conf"
    conf.write_text("[agent]\ninterval = 5\nservices = a, b\nport = x\ntoken = s\nplugin_timeout = 0\n")
    cfg = agent.load_config(str(conf))
    assert cfg["interval"] == 5 and cfg["services"] == ["a", "b"]
    assert cfg["port"] == 0 and cfg["token"] == "s" and cfg["plugin_timeout"] == 1


def test_proc_collectors(tmp_path, monkeypatch):
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 100 kB\nSwapFree: 60 kB\n")
    (tmp_path / "cpuinfo").write_text("processor : 0\nmodel name : X\nprocessor : 1\nmodel name : X\n")
    (tmp_path / "loadavg").write_text("0.1 0.2 0.3 1/2 3\n")
    (tmp_path / "uptime").write_text("100.5 50\n")
    monkeypatch.setattr(agent, "PROC", str(tmp_path))
    mem = agent.collect_memory()
    assert mem["used_bytes"] == 600 * 1024 and mem["swap_used_bytes"] == 40 * 1024
    cpu = agent.collect_cpu()
    assert cpu["cores"] == 2 and cpu["model"] == "X" and cpu["load_average"] == [0.1, 0.2, 0.3]
    assert agent.collect_uptime()["uptime_seconds"] == 100


def test_os_release(tmp_path, monkeypatch):
    f = tmp_path / "os-release"
    f.write_text('NAME="Debian"\nPRETTY_NAME="Debian 12"\nID=debian\n')
    monkeypatch.setattr(agent, "OS_RELEASE", (str(f),))
    assert agent.collect_os()["pretty_name"] == "Debian 12"


def test_services_mocked(monkeypatch):
    monkeypatch.setattr(agent, "_run", lambda cmd, timeout=30: (0, "active\n") if cmd[-1] == "ssh" else (3, "inactive\n"))
    assert agent.collect_services(["ssh", "x"]) == {"ssh": "active", "x": "inactive"}


def test_network_fallback(monkeypatch):
    out = json.dumps([{"ifname": "eth0", "address": "aa", "operstate": "UP",
                       "addr_info": [{"local": "10.0.0.2", "prefixlen": 24}]}])
    monkeypatch.setattr(agent, "_run", lambda cmd, timeout=30: (0, out))
    assert agent.collect_network()["eth0"]["addresses"] == ["10.0.0.2/24"]


def _plugin(path, body, mode=0o755):
    path.write_text("#!/bin/sh\n" + body + "\n")
    os.chmod(path, mode)


def _as_root(monkeypatch):
    real = os.stat

    class S:
        def __init__(self, st):
            self.st_mode, self.st_uid = st.st_mode, 0
    monkeypatch.setattr(agent.os, "stat", lambda p: S(real(p)))


def test_plugins(tmp_path, monkeypatch):
    _as_root(monkeypatch)
    _plugin(tmp_path / "good.sh", "echo '{\"a\": 1}'")
    _plugin(tmp_path / "bad", "echo nope")
    _plugin(tmp_path / "slow", "sleep 5")
    _plugin(tmp_path / "fail", "exit 2")
    _plugin(tmp_path / "ww", "echo '{}'", 0o777)
    _plugin(tmp_path / "noexec", "echo '{}'", 0o644)
    _plugin(tmp_path / "x.example", "echo '{}'")
    res = agent.run_plugins(str(tmp_path), 1)
    assert res["good"] == {"a": 1}
    assert res["bad"] == {"error": "invalid json"}
    assert res["slow"] == {"error": "timeout"}
    assert "exit code" in res["fail"]["error"]
    assert "error" in res["ww"]
    assert "noexec" not in res and "x" not in res


def test_plugin_not_root_skipped(tmp_path):
    if os.getuid() == 0:
        pytest.skip("laeuft als root")
    _plugin(tmp_path / "p", "echo '{}'")
    assert "error" in agent.run_plugins(str(tmp_path), 1)["p"]


def test_collect_all_merges(tmp_path, monkeypatch):
    for name in list(vars(agent)):
        if name.startswith("collect_") and name != "collect_all":
            monkeypatch.setattr(agent, name, lambda *a: {} if name != "collect_x" else None)
    monkeypatch.setattr(agent, "run_plugins", lambda d, t: {"d": d})
    cfg = dict(agent.DEFAULTS, plugin_dir="P", local_dir="L")
    data = agent.collect_all(cfg)
    assert data["plugins"] == {"d": "P"} and data["local"] == {"d": "L"}


def test_write_atomic(tmp_path):
    p = tmp_path / "sub" / "i.json"
    agent.write_atomic(str(p), "{}")
    assert p.read_text() == "{}" and stat.S_IMODE(p.stat().st_mode) == 0o644


def test_http_token(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    snap = agent.Snapshot()
    snap.set({"ok": 1})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler(snap, "sekret"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/info"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url)
    assert err.value.code == 401
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + "sekret"})
    assert json.load(urllib.request.urlopen(req)) == {"ok": 1}
    srv.shutdown()
