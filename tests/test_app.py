import os
import re

import pytest
from werkzeug.security import generate_password_hash

from app import create_app


@pytest.fixture
def client():
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True,
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    return app.test_client()


def token(c, url="/login"):
    html = c.get(url).get_data(as_text=True)
    return re.search(r'name="csrf" value="(\w+)"', html).group(1)


def test_protected_redirects(client):
    for path in ("/", "/nodes", "/guests", "/storage", "/status", "/kanban",
                 "/api/kanban/todos"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_bad_password(client):
    r = client.post("/login", data={"username": "admin", "password": "no", "csrf": token(client)})
    assert r.status_code == 200 and "falsch" in r.get_data(as_text=True)


def test_missing_csrf(client):
    assert client.post("/login", data={"username": "admin", "password": "pw"}).status_code == 400


def test_login_and_logout(client, monkeypatch):
    from app import proxmox
    monkeypatch.setattr(proxmox.ProxmoxClient, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(proxmox.ProxmoxClient, "cluster_status", lambda s: [])
    r = client.post("/login", data={"username": "admin", "password": "pw", "csrf": token(client)})
    assert r.status_code == 302
    assert "n1" in client.get("/nodes").get_data(as_text=True)
    client.post("/logout", data={"csrf": token(client, "/nodes")})
    assert client.get("/nodes").status_code == 302


def test_no_open_redirect(client):
    r = client.post("/login?next=//evil.com", data={"username": "admin", "password": "pw", "csrf": token(client)})
    assert r.headers["Location"] == "/"


def test_login_redirect_uses_known_internal_route(client):
    r = client.post("/login?next=/kanban", data={
        "username": "admin", "password": "pw", "csrf": token(client),
    })
    assert r.headers["Location"] == "/kanban"


def test_only_get_in_client():
    import inspect
    from app import proxmox
    src = inspect.getsource(proxmox)
    assert not re.search(r"requests\.(put|delete|patch)", src)
    assert not re.search(r"requests\.post", src)


def test_group_guests_by_node():
    from app import group_guests_by_node
    tree = group_guests_by_node([{"node": "a"}, {"node": "b"}],
                                [{"node": "a", "vmid": 2}, {"node": "a", "vmid": 1}, {"node": "c", "vmid": 3}])
    assert [t["name"] for t in tree] == ["a", "b", "c"]
    assert [g["vmid"] for g in tree[0]["guests"]] == [1, 2]
    assert tree[1]["guests"] == []


def test_guests_tree_page(client, monkeypatch):
    from app import proxmox
    monkeypatch.setattr(proxmox.ProxmoxClient, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(proxmox.ProxmoxClient, "guests",
                        lambda s: [{"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": "running"}])
    client.post("/login", data={"username": "admin", "password": "pw", "csrf": token(client)})
    html = client.get("/guests").get_data(as_text=True)
    assert "n1" in html and "web" in html and "<details" in html


def test_build_status():
    from app import build_status
    st = build_status([{"type": "cluster", "quorate": 0, "name": "c"}],
                      [{"node": "a", "status": "online"}, {"node": "b", "status": "offline"}],
                      {"a": {"version": "8.1"}, "b": {"version": "8.2"}}, False)
    assert st["quorate"] is False and len(st["warnings"]) == 4
    assert build_status([], [], {}, True)["warnings"] == []


def test_status_page(client, monkeypatch):
    from app import proxmox
    monkeypatch.setattr(proxmox.ProxmoxClient, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(proxmox.ProxmoxClient, "cluster_status", lambda s: [])
    def boom(s, n):
        raise proxmox.ProxmoxError("x")
    monkeypatch.setattr(proxmox.ProxmoxClient, "node_version", boom)
    client.post("/login", data={"username": "admin", "password": "pw", "csrf": token(client)})
    html = client.get("/status").get_data(as_text=True)
    assert "n1" in html and "Info / Hinweis" in html and "Status</a>" in html


def test_extract_ips():
    from app.guestinfo import extract_lxc_ips, extract_qemu_ips
    data = {"result": [
        {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},
        {"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5"}, {"ip-address": "fe80::1"},
                                          {"ip-address": "::1"}, {"ip-address": "2001:db8::2"}]}]}
    assert extract_qemu_ips(data) == ["10.0.0.5", "2001:db8::2"]
    assert extract_qemu_ips(None) == []
    assert extract_lxc_ips([{"name": "lo", "inet": "127.0.0.1/8"},
                            {"name": "eth0", "inet": "10.0.0.6/24", "inet6": "fe80::2/64"}]) == ["10.0.0.6"]


def test_services_and_ports():
    from app.guestinfo import detect_services, parse_service_ports
    ports = parse_service_ports("22:ssh,80:http,x:bad,443,ssh:22,http:80")
    assert ports == [(22, "ssh"), (80, "http"), (443, "443"), (22, "ssh"), (80, "http")]
    assert detect_services("1.2.3.4", ports, checker=lambda ip, p, t: p == 22) == ["ssh", "ssh"]
    assert detect_services(None, ports) == []


def test_host_info_limit():
    from app.guestinfo import clean_host_info
    assert clean_host_info({"content": "a" * 10000}) == "a" * 8192
    assert clean_host_info({}) is None


def test_comment_store(tmp_path):
    from app.guestinfo import CommentStore
    s = CommentStore(str(tmp_path / "sub" / "c.json"))
    s.set(100, " hallo ")
    assert s.get(100) == "hallo"
    s.set(100, "")
    assert s.get(100) == ""
    with pytest.raises(ValueError):
        s.set(1, "x" * 2001)


def _login(c):
    c.post("/login", data={"username": "admin", "password": "pw", "csrf": token(c)})


def test_comment_requires_login_and_csrf(tmp_path):
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True, "COMMENTS_DB": str(tmp_path / "c.json"),
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    r = c.post("/guests/100/comment", data={"comment": "x"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    _login(c)
    assert c.post("/guests/100/comment", data={"comment": "x"}).status_code == 400
    r = c.post("/guests/100/comment", data={"comment": "<b>hi</b>", "csrf": token(c, "/guests")})
    assert r.status_code == 302
    from app.guestinfo import CommentStore
    assert CommentStore(str(tmp_path / "c.json")).get(100).endswith("admin: <b>hi</b>")
    stored = CommentStore(str(tmp_path / "c.json")).get(100)
    assert stored.startswith("[") and "] admin: " in stored
    c.post("/guests/100/comment", data={"comment": stored, "csrf": token(c, "/guests")})
    assert CommentStore(str(tmp_path / "c.json")).get(100) == stored
    c.post("/guests/100/comment", data={"comment": stored + "\nzwei", "csrf": token(c, "/guests")})
    lines = CommentStore(str(tmp_path / "c.json")).get(100).split("\n")
    assert lines[1] == stored and lines[0].endswith("admin: zwei")


def test_comment_new_entry_on_top_without_double_prefix():
    from datetime import datetime
    from app.guestinfo import apply_comment_prefix
    now = datetime(2024, 1, 2, 3, 4)
    old = "[2023-01-01 10:00] admin: alt"
    out = apply_comment_prefix(old, "[2024-01-02 03:04] admin: neu\n" + old, "admin", now)
    assert out == "[2024-01-02 03:04] admin: neu\n" + old
    assert apply_comment_prefix(old, "[2024-01-02 03:04] admin: \n" + old, "admin", now) == old
    out = apply_comment_prefix(old, old + "\nunten", "admin", now)
    assert out.split("\n") == ["[2024-01-02 03:04] admin: unten", old]


def test_guests_template_has_new_button():
    from pathlib import Path
    t = Path("app/templates/guests.html").read_text()
    assert 'class="comment-new">Neu</button>' in t
    assert "comment-new" in Path("app/static/guests.js").read_text()


def test_guests_page_enriched(tmp_path, monkeypatch):
    from app import proxmox
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True, "COMMENTS_DB": str(tmp_path / "c.json"),
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    P = proxmox.ProxmoxClient
    monkeypatch.setattr("app.detect_services", lambda ip, ports: [])
    monkeypatch.setattr(P, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(P, "guests", lambda s: [
        {"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": "running"},
        {"node": "n1", "vmid": 101, "name": "ct", "type": "lxc", "status": "running"}])
    monkeypatch.setattr(P, "qemu_interfaces", lambda s, n, v: {"result": [
        {"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5"}]}]})
    monkeypatch.setattr(P, "qemu_file_read", lambda s, n, v, p: {"content": "<script>x</script>"})
    def fail(s, n, v):
        raise proxmox.ProxmoxError("kein Zugriff")
    monkeypatch.setattr(P, "lxc_interfaces", fail)
    _login(c)
    html = c.get("/guests").get_data(as_text=True)
    assert "10.0.0.5" in html and "unbekannt" in html
    assert "&lt;script&gt;x" in html and "<script>x" not in html


def test_kanban_todo_api_crud_and_validation(tmp_path):
    app = create_app({
        "SECRET_KEY": "x" * 32, "TESTING": True,
        "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
        "KANBAN_TODOS_DB": str(tmp_path / "todos.json"),
    })
    c = app.test_client()
    assert c.post("/api/kanban/todos", json={"title": "Aufgabe"}).status_code == 302
    _login(c)
    assert c.post("/api/kanban/todos", json={"title": "Aufgabe"}).status_code == 400
    csrf = token(c, "/kanban")
    headers = {"X-CSRF-Token": csrf}
    created = c.post("/api/kanban/todos", json={
        "title": "Wartung", "description": "Host prüfen", "vmid": 100,
    }, headers=headers)
    assert created.status_code == 201
    todo = created.get_json()
    assert todo["title"] == "Wartung" and todo["vmid"] == "100"
    assert todo["status"] == "planned" and todo["created_at"]
    assert c.get("/api/kanban/todos").get_json() == [todo]
    assert c.put(f"/api/kanban/todos/{todo['id']}", json={"status": "done", "comment": "ok"},
                 headers=headers).get_json()["status"] == "done"
    assert c.put(f"/api/kanban/todos/{todo['id']}", json={"status": "invalid", "comment": "x"},
                 headers=headers).status_code == 400
    assert c.delete(f"/api/kanban/todos/{todo['id']}", json={"comment": "weg"}, headers=headers).status_code == 204
    assert c.get("/api/kanban/todos").get_json() == []


def test_kanban_page_host_info_and_proxmox_failure(tmp_path, monkeypatch):
    from app import proxmox
    app = create_app({
        "SECRET_KEY": "x" * 32, "TESTING": True,
        "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
        "KANBAN_TODOS_DB": str(tmp_path / "todos.json"),
        "HOST_INFO_FILE": str(tmp_path / "host.info"),
    })
    (tmp_path / "host.info").write_text("<script>nicht ausführen</script>\n", encoding="utf-8")

    def unavailable(_):
        raise proxmox.ProxmoxError("Proxmox nicht erreichbar")

    monkeypatch.setattr(proxmox.ProxmoxClient, "guests", unavailable)
    c = app.test_client()
    _login(c)
    html = c.get("/kanban").get_data(as_text=True)
    assert "Proxmox nicht erreichbar" in html
    assert "&lt;script&gt;nicht ausführen&lt;/script&gt;" in html
    assert "<script>nicht ausführen</script>" not in html
    assert "Geplant / ToDo" in html and "In Arbeit" in html and "Erledigt" in html


def test_read_host_info_is_optional_and_limited(tmp_path):
    from app.kanban import read_host_info
    missing = tmp_path / "missing.info"
    assert read_host_info(str(missing)) is None
    path = tmp_path / "host.info"
    path.write_bytes(b"a" * (64 * 1024 + 10))
    assert len(read_host_info(str(path))) == 64 * 1024


def test_kanban_shows_guest_host_info(tmp_path, monkeypatch):
    from app import proxmox
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True,
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
                      "KANBAN_TODOS_DB": str(tmp_path / "todos.json"),
                      "HOST_INFO_FILE": str(tmp_path / "none.info")})
    monkeypatch.setattr(proxmox.ProxmoxClient, "guests", lambda self: [
        {"vmid": 100, "name": "a", "node": "n", "type": "qemu", "status": "running"},
        {"vmid": 101, "name": "b", "node": "n", "type": "qemu", "status": "stopped"}])
    monkeypatch.setattr(proxmox.ProxmoxClient, "qemu_interfaces", lambda self, n, v: [])
    monkeypatch.setattr(proxmox.ProxmoxClient, "qemu_file_read",
                        lambda self, n, v, p: {"content": "<i>Zeile1</i>\nZeile2"})
    c = app.test_client()
    _login(c)
    html = c.get("/kanban").get_data(as_text=True)
    assert "&lt;i&gt;Zeile1&lt;/i&gt;\nZeile2" in html
    assert "Keine Host-Info" in html
    assert "hover-trigger" not in html
    assert "class=\"hostname\" tabindex=\"0\"" in html


def test_update_status_helpers():
    from app.guestinfo import update_status
    files = {"/etc/os-release": {"content": 'ID=ubuntu\nID_LIKE=debian\n'},
             "/var/run/reboot-required": {"content": "*** System restart required ***"},
             "/var/lib/update-notifier/updates-available": {"content": "\n5 updates can be applied immediately.\n"}}

    def reader(path):
        if path not in files:
            raise ValueError("nicht gefunden")
        return files[path]
    assert update_status(reader) == (True, True)
    del files["/var/run/reboot-required"]
    files["/var/lib/update-notifier/updates-available"] = {"content": "0 updates can be applied."}
    assert update_status(reader) == (False, False)
    del files["/var/lib/update-notifier/updates-available"]
    assert update_status(reader) == (None, False)
    files["/etc/os-release"] = {"content": "ID=alpine"}
    assert update_status(reader) == (None, None)
    assert update_status(lambda p: (_ for _ in ()).throw(TimeoutError())) == (None, None)


def test_guests_page_update_status(tmp_path, monkeypatch):
    from app import proxmox
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True, "COMMENTS_DB": str(tmp_path / "c.json"),
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    P = proxmox.ProxmoxClient
    monkeypatch.setattr("app.detect_services", lambda ip, ports: [])
    monkeypatch.setattr(P, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(P, "guests", lambda s: [
        {"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": "running"},
        {"node": "n1", "vmid": 101, "name": "ct", "type": "lxc", "status": "running"}])
    monkeypatch.setattr(P, "qemu_interfaces", lambda s, n, v: [])

    def read(s, n, v, p):
        if p == "/etc/os-release":
            return {"content": "ID=debian"}
        if p == "/var/run/reboot-required":
            return {"content": "x"}
        raise proxmox.ProxmoxError("fehlt")
    monkeypatch.setattr(P, "qemu_file_read", read)
    monkeypatch.setattr(P, "lxc_interfaces", lambda s, n, v: [])
    _login(c)
    html = c.get("/guests").get_data(as_text=True)
    assert "Update vorhanden: Unbekannt" in html
    assert html.count("Reboot nötig: Ja") == 1 and html.count("Reboot nötig: Unbekannt") == 1


def test_missing_services():
    from app import guestinfo
    ports = guestinfo.parse_service_ports("22:ssh,80:http,5432:postgres")
    assert guestinfo.missing_services(["ssh", "postgres", "x"], ports, ["ssh"]) == ["postgres"]
    assert guestinfo.missing_services([], ports, []) == []


def test_env_service_config_removed(monkeypatch):
    monkeypatch.setenv("SERVICE_CHECK", "false")
    monkeypatch.setenv("REQUIRED_SERVICES", "*:all")
    app = create_app({"SECRET_KEY": "x" * 32, "ADMIN_PASSWORD_HASH": "h"})
    for key in ("SERVICE_CHECK", "SERVICE_PORTS", "AUTO_TODO_ENABLED", "REQUIRED_SERVICES"):
        assert key not in app.config
    example = open(os.path.join(os.path.dirname(__file__), "..", ".env.example")).read()
    for key in ("SERVICE_CHECK", "SERVICE_PORTS", "AUTO_TODO_ENABLED", "REQUIRED_SERVICES",
                "SERVICE_MONITOR_INTERVAL"):
        assert key not in example


def _services_app(tmp_path, monkeypatch):
    import app as app_module
    app = create_app({
        "SECRET_KEY": "x" * 32, "TESTING": True,
        "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
        "KANBAN_TODOS_DB": str(tmp_path / "t.json"),
        "SERVICE_MONITOR_DB": str(tmp_path / "s.json"),
    })

    class FakeClient:
        def guests(self):
            return [{"vmid": 100, "name": "web", "type": "qemu", "node": "n", "status": "running"},
                    {"vmid": 101, "name": "off", "type": "qemu", "node": "n", "status": "stopped"}]
        def qemu_interfaces(self, node, vmid):
            return [{"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5"}]}]
        def qemu_file_read(self, *a):
            raise app_module.ProxmoxError("x")
    monkeypatch.setattr(app_module, "ProxmoxClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(app_module, "detect_services", lambda ip, ports: ["ssh"])
    monkeypatch.setattr(app_module, "update_status", lambda f: (None, None))
    c = app.test_client()
    _login(c)
    return app, c


def test_services_tab_persistence_and_csrf(tmp_path, monkeypatch):
    app, c = _services_app(tmp_path, monkeypatch)
    html = c.get("/services").get_data(as_text=True)
    assert "Dienstüberwachung" in html and "web" in html and 'value="http"' in html
    assert c.post("/services", data={"vmid": "100", "svc-100": "http"}).status_code == 400
    r = c.post("/services", data={"csrf": token(c, "/services"), "vmid": ["100", "101"],
                                  "svc-100": ["http", "bogus"]})
    assert r.status_code == 302
    from app.guestinfo import ServiceMonitorStore
    assert ServiceMonitorStore(str(tmp_path / "s.json")).all() == {"100": ["http"]}
    assert re.search(r'value="http"[^>]*checked', c.get("/services").get_data(as_text=True))
    anon = app.test_client()
    assert anon.get("/services").status_code == 302


def test_auto_todo_from_gui_selection_without_duplicates(tmp_path, monkeypatch):
    app, c = _services_app(tmp_path, monkeypatch)
    for _ in range(2):
        assert c.get("/kanban").status_code == 200
    assert c.get("/api/kanban/todos").get_json() == []
    c.post("/services", data={"csrf": token(c, "/services"), "vmid": ["100", "101"],
                              "svc-100": ["ssh", "http"], "svc-101": ["http"]})
    for _ in range(2):
        assert c.get("/kanban").status_code == 200
    todos = c.get("/api/kanban/todos").get_json()
    assert len(todos) == 1
    assert todos[0]["status"] == "planned" and todos[0]["vmid"] == "100"
    assert "http" in todos[0]["title"]


def test_disabling_service_closes_auto_todo_with_comment(tmp_path, monkeypatch):
    app, c = _services_app(tmp_path, monkeypatch)
    c.post("/services", data={"csrf": token(c, "/services"), "vmid": ["100"], "svc-100": ["ssh", "http"]})
    c.get("/kanban")
    other = c.post("/api/kanban/todos", json={"title": "manuell", "vmid": "100"},
                   headers={"X-CSRF-Token": token(c, "/kanban")}).get_json()
    todos = c.get("/api/kanban/todos").get_json()
    assert len(todos) == 2
    c.post("/services", data={"csrf": token(c, "/services"), "vmid": ["100"], "svc-100": ["ssh"]})
    by_id = {t["id"]: t for t in c.get("/api/kanban/todos").get_json()}
    auto = next(t for t in by_id.values() if t.get("auto_key"))
    assert auto["status"] == "done"
    assert auto["comments"][0]["author"] == "admin"
    assert "http" in auto["comments"][0]["text"] and "nicht benötigt" in auto["comments"][0]["text"]
    assert by_id[other["id"]]["status"] == "planned" and "comments" not in by_id[other["id"]]


def _bulk_setup(tmp_path, n=3):
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
                      "KANBAN_TODOS_DB": str(tmp_path / "todos.json")})
    c = app.test_client()
    _login(c)
    h = {"X-CSRF-Token": token(c, "/kanban")}
    ids = [c.post("/api/kanban/todos", json={"title": f"T{i}"}, headers=h).get_json()["id"] for i in range(n)]
    return c, h, ids


def _by_id(c):
    return {t["id"]: t for t in c.get("/api/kanban/todos").get_json()}


def test_bulk_move_edit_done_and_untouched(tmp_path):
    c, h, ids = _bulk_setup(tmp_path)
    url = "/api/kanban/todos/bulk"
    r = c.post(url, json={"ids": ids[:2], "action": "move", "status": "in_progress", "comment": "los"}, headers=h)
    assert r.status_code == 200 and all(x["ok"] for x in r.get_json()["results"])
    todos = _by_id(c)
    assert [todos[i]["status"] for i in ids] == ["in_progress", "in_progress", "planned"]
    assert len(todos[ids[2]]["history"]) == 1
    entry = todos[ids[0]]["history"][-1]
    assert entry["action"] == "move" and entry["comment"] == "los" and entry["user"] == "admin" and entry["time"]
    assert c.post(url, json={"ids": ids[:2], "action": "edit", "title": "Neu", "vmid": "5",
                             "comment": "k"}, headers=h).status_code == 200
    assert c.post(url, json={"ids": [ids[0]], "action": "done", "comment": "fertig"}, headers=h).status_code == 200
    todos = _by_id(c)
    assert todos[ids[0]]["title"] == "Neu" and todos[ids[0]]["vmid"] == "5" and todos[ids[0]]["status"] == "done"
    assert todos[ids[1]]["status"] == "in_progress" and todos[ids[2]]["title"] == "T2"
    r = c.post(url, json={"ids": [ids[0], "nope"], "action": "move", "status": "planned", "comment": "x"}, headers=h)
    assert [x["ok"] for x in r.get_json()["results"]] == [True, False]


def test_bulk_delete_keeps_audit_log(tmp_path):
    c, h, ids = _bulk_setup(tmp_path)
    r = c.post("/api/kanban/todos/bulk", json={"ids": ids[:2], "action": "delete", "comment": "weg"}, headers=h)
    assert r.status_code == 200
    assert list(_by_id(c)) == [ids[2]]
    import json
    log = json.loads((tmp_path / "todos-audit.json").read_text(encoding="utf-8"))
    assert {i["id"] for i in log} == set(ids[:2])
    assert log[0]["history"][-1]["action"] == "delete" and log[0]["history"][-1]["user"] == "admin"


def test_bulk_requires_comment_and_valid_input(tmp_path):
    c, h, ids = _bulk_setup(tmp_path, 1)
    url = "/api/kanban/todos/bulk"
    for comment in (None, "", "   ", 5):
        body = {"ids": ids, "action": "done"}
        if comment is not None:
            body["comment"] = comment
        assert c.post(url, json=body, headers=h).status_code == 400
    assert c.post(url, json={"ids": ids, "action": "bogus", "comment": "x"}, headers=h).status_code == 400
    assert c.post(url, json={"ids": [], "action": "done", "comment": "x"}, headers=h).status_code == 400
    assert c.post(url, json={"ids": ids, "action": "move", "status": "x", "comment": "x"}, headers=h).status_code == 400
    assert c.post(url, json={"ids": ids, "action": "edit", "comment": "x"}, headers=h).status_code == 400
    assert c.put(f"/api/kanban/todos/{ids[0]}", json={"status": "done"}, headers=h).status_code == 400
    assert c.delete(f"/api/kanban/todos/{ids[0]}", headers=h).status_code == 400
    assert _by_id(c)[ids[0]]["status"] == "planned"


def test_history_is_server_generated_and_immutable(tmp_path):
    c, h, ids = _bulk_setup(tmp_path, 1)
    forged = {"time": "2000-01-01T00:00:00+00:00", "user": "mallory", "action": "x", "comment": "f"}
    c.post("/api/kanban/todos/bulk", json={"ids": ids, "action": "done", "comment": "ok", "user": "mallory",
                                           "time": forged["time"], "history": [forged]}, headers=h)
    c.put(f"/api/kanban/todos/{ids[0]}", json={"title": "N", "comment": "ed", "user": "mallory",
                                               "history": [forged], "time": forged["time"]}, headers=h)
    history = _by_id(c)[ids[0]]["history"]
    assert [e["action"] for e in history] == ["create", "done", "edit"]
    assert all(e["user"] == "admin" and e["time"].startswith(("202", "203")) for e in history)
    assert forged not in history


def test_bulk_requires_login_and_csrf(tmp_path):
    c, h, ids = _bulk_setup(tmp_path, 1)
    body = {"ids": ids, "action": "done", "comment": "x"}
    assert c.post("/api/kanban/todos/bulk", json=body).status_code == 400
    assert c.post("/api/kanban/todos/bulk", json=body, headers={"X-CSRF-Token": "bad"}).status_code == 400
    assert _by_id(c)[ids[0]]["status"] == "planned"
    anon = c.application.test_client()
    assert anon.post("/api/kanban/todos/bulk", json=body, headers=h).status_code == 302


def _info_app(tmp_path, monkeypatch):
    from app import proxmox
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True, "COMMENTS_DB": str(tmp_path / "c.json"),
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    P = proxmox.ProxmoxClient
    monkeypatch.setattr("app.detect_services", lambda ip, ports: [])
    monkeypatch.setattr(P, "nodes", lambda s: [{"node": "n1", "status": "online"}])
    monkeypatch.setattr(P, "guests", lambda s: [
        {"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": "running"},
        {"node": "n1", "vmid": 101, "name": "ct", "type": "lxc", "status": "running"}])
    monkeypatch.setattr(P, "qemu_interfaces", lambda s, n, v: {"result": [
        {"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5"}]}]})
    monkeypatch.setattr(P, "lxc_interfaces", lambda s, n, v: [
        {"name": "eth0", "inet": "10.0.0.6/24"}])
    monkeypatch.setattr(P, "qemu_file_read", lambda s, n, v, p: {"content": "<b>" + p + "</b>"})
    return c


def test_hostname_is_bordered_clickable(tmp_path, monkeypatch):
    c = _info_app(tmp_path, monkeypatch)
    _login(c)
    html = c.get("/guests").get_data(as_text=True)
    assert 'class="hostname guest-hostname"' in html
    assert 'href="/guests/100"' in html
    assert "border:2px solid" in html


def test_guest_info_requires_login(tmp_path, monkeypatch):
    c = _info_app(tmp_path, monkeypatch)
    assert c.get("/guests/100/info").status_code == 302


def test_guest_info_qemu_and_lxc(tmp_path, monkeypatch):
    c = _info_app(tmp_path, monkeypatch)
    _login(c)
    d = c.get("/guests/100/info").get_json()
    assert d["type"] == "qemu" and d["ips"] == ["10.0.0.5"]
    assert d["agent_info"] == "<b>/var/lib/prox-agent/info.json</b>"
    assert d["host_info"] == "<b>/srv/info/host.info</b>"
    d = c.get("/guests/101/info").get_json()
    assert d["type"] == "lxc" and d["ips"] == ["10.0.0.6"]
    assert d["agent_info"] is None and d["note"]
    assert c.get("/guests/999/info").status_code == 404


def test_guest_js_has_no_inline_fetch():
    from pathlib import Path
    assert "fetch(" not in Path("app/static/guests.js").read_text()


def _detail_app(tmp_path, monkeypatch, status="running"):
    from app import proxmox
    c = _info_app(tmp_path, monkeypatch)
    P = proxmox.ProxmoxClient
    monkeypatch.setattr(P, "guests", lambda s: [
        {"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": status},
        {"node": "n1", "vmid": 101, "name": "ct", "type": "lxc", "status": status}])
    monkeypatch.setattr(P, "qemu_hostname", lambda s, n, v: {"result": {"host-name": "<i>vmhost</i>"}})
    monkeypatch.setattr(P, "qemu_interfaces", lambda s, n, v: {"result": [
        {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},
        {"name": "eth0", "hardware-address": "aa:bb:cc:dd:ee:ff",
         "ip-addresses": [{"ip-address": "10.0.0.5"}]}]})
    monkeypatch.setattr(P, "qemu_fsinfo", lambda s, n, v: {"result": [
        {"mountpoint": "/", "type": "ext4", "used-bytes": 1073741824, "total-bytes": 4294967296},
        {"mountpoint": "/run", "type": "tmpfs", "used-bytes": 1, "total-bytes": 2}]})
    monkeypatch.setattr(P, "qemu_file_read", lambda s, n, v, p: {
        "content": "0.10 0.20 0.30 1/100 5\n" if "loadavg" in p else "processor\t: 0\nprocessor\t: 1\n"})
    monkeypatch.setattr(P, "qemu_status", lambda s, n, v: {"cpu": 0.25})
    _login(c)
    return c


def test_guest_detail_requires_login(tmp_path, monkeypatch):
    c = _info_app(tmp_path, monkeypatch)
    assert c.get("/guests/100").status_code == 302


def test_guest_detail_qemu_renders_all_sections(tmp_path, monkeypatch):
    c = _detail_app(tmp_path, monkeypatch)
    html = c.get("/guests/100").get_data(as_text=True)
    assert "&lt;i&gt;vmhost&lt;/i&gt;" in html and "<i>vmhost</i>" not in html
    assert "10.0.0.5" in html and "aa:bb:cc:dd:ee:ff" in html and "127.0.0.1" not in html
    assert "ext4" in html and "1.0 GiB" in html and "4.0 GiB" in html and "25.0 %" in html
    assert "tmpfs" not in html
    assert "0.1 / 0.2 / 0.3" in html and "CPUs: 2" in html


def test_guest_detail_unknown_vmid_404(tmp_path, monkeypatch):
    c = _detail_app(tmp_path, monkeypatch)
    assert c.get("/guests/999").status_code == 404


def test_guest_detail_partial_failure(tmp_path, monkeypatch):
    from app import proxmox
    c = _detail_app(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise proxmox.ProxmoxError("x")
    monkeypatch.setattr(proxmox.ProxmoxClient, "qemu_fsinfo", boom)
    monkeypatch.setattr(proxmox.ProxmoxClient, "qemu_hostname", boom)
    r = c.get("/guests/100")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Guest Agent nicht erreichbar" in html
    assert "10.0.0.5" in html and "0.1 / 0.2 / 0.3" in html


def test_guest_detail_lxc_and_stopped(tmp_path, monkeypatch):
    c = _detail_app(tmp_path, monkeypatch)
    html = c.get("/guests/101").get_data(as_text=True)
    assert "10.0.0.6" in html and "nicht unterstützt" in html
    c = _detail_app(tmp_path, monkeypatch, status="stopped")
    assert "Gast läuft nicht." in c.get("/guests/100").get_data(as_text=True)


def _full_app(tmp_path, monkeypatch):
    from app import proxmox
    c = _detail_app(tmp_path, monkeypatch)
    P = proxmox.ProxmoxClient
    monkeypatch.setattr(P, "qemu_osinfo", lambda s, n, v: {"result": {"pretty-name": "Debian <b>12</b>", "kernel-release": "6.1"}})
    monkeypatch.setattr(P, "qemu_users", lambda s, n, v: {"result": [
        {"user": "<script>root</script>", "login-time": 1700000000.5}]})
    monkeypatch.setattr(P, "qemu_time", lambda s, n, v: 1700000000000000000)
    monkeypatch.setattr(P, "qemu_fsfreeze_status", lambda s, n, v: "thawed")
    base_read = P.qemu_file_read
    reads = []

    def read(s, n, v, p):
        reads.append(p)
        if p == "/srv/info/host_info":
            return {"content": "foo: bar\napt-update: 2024-05-01 10:11:12.123456789 +0200\n"}
        return base_read(s, n, v, p)
    monkeypatch.setattr(P, "qemu_file_read", read)
    c.reads = reads
    monkeypatch.setattr(P, "qemu_config", lambda s, n, v: {
        "name": "<i>web</i>", "memory": 2048, "cipassword": "s3cret", "sshkeys": "ssh-rsa%20AAA",
        "Password": "hunter2"})
    monkeypatch.setattr(P, "lxc_config", lambda s, n, v: {"hostname": "ct", "password": "pw123"})
    return c


def test_guest_detail_new_sections(tmp_path, monkeypatch):
    c = _full_app(tmp_path, monkeypatch)
    html = c.get("/guests/100").get_data(as_text=True)
    for label in ("Betriebssystem", "Angemeldete Benutzer", "Gastzeit", "Dateisystem-Freeze",
                  "Letztes apt-Update", "VM-Konfiguration (qm config)"):
        assert label in html
    assert "Debian &lt;b&gt;12&lt;/b&gt;" in html and "Debian <b>12</b>" not in html
    assert "&lt;script&gt;root" in html and "<script>root" not in html
    assert "14.11.2023 22:13:20" in html
    assert "Nicht eingefroren" in html
    assert "01.05.2024 10:11:12 (+0200)" in html
    assert "&lt;i&gt;web&lt;/i&gt;" in html and "2048" in html
    assert "s3cret" not in html and "ssh-rsa" not in html and "hunter2" not in html and "********" in html


def test_guest_detail_new_sections_partial_failure(tmp_path, monkeypatch):
    from app import proxmox
    c = _full_app(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise proxmox.ProxmoxError("x")
    for name in ("qemu_osinfo", "qemu_users", "qemu_config"):
        monkeypatch.setattr(proxmox.ProxmoxClient, name, boom)
    r = c.get("/guests/100")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Konfiguration nicht erreichbar" in html
    assert "14.11.2023" in html and "01.05.2024" in html and "10.0.0.5" in html


def test_apt_update_reads_host_info_file(tmp_path, monkeypatch):
    c = _full_app(tmp_path, monkeypatch)
    html = c.get("/guests/100").get_data(as_text=True)
    assert "/srv/info/host_info" in c.reads and "01.05.2024 10:11:12 (+0200)" in html
    assert "guest exec" not in html


def test_apt_update_fallback_missing_or_malformed(tmp_path, monkeypatch):
    from app import proxmox
    c = _full_app(tmp_path, monkeypatch)
    P = proxmox.ProxmoxClient
    base = P.qemu_file_read

    def missing(s, n, v, p):
        if p == "/srv/info/host_info":
            raise proxmox.ProxmoxError("x")
        return base(s, n, v, p)
    monkeypatch.setattr(P, "qemu_file_read", missing)
    html = c.get("/guests/100").get_data(as_text=True)
    assert "Letztes apt-Update" in html and "Nicht verfügbar" in html and "14.11.2023" in html
    for content in ("apt: 2024-05-01\n", "apt-update:   \n", ""):
        monkeypatch.setattr(P, "qemu_file_read", lambda s, n, v, p, c_=content: (
            {"content": c_} if p == "/srv/info/host_info" else base(s, n, v, p)))
        assert "Nicht verfügbar" in c.get("/guests/100").get_data(as_text=True)


def test_apt_update_value_escaped(tmp_path, monkeypatch):
    from app import proxmox
    c = _full_app(tmp_path, monkeypatch)
    P = proxmox.ProxmoxClient
    base = P.qemu_file_read
    monkeypatch.setattr(P, "qemu_file_read", lambda s, n, v, p: (
        {"content": "apt-update: <b>x</b>"} if p == "/srv/info/host_info" else base(s, n, v, p)))
    html = c.get("/guests/100").get_data(as_text=True)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_no_guest_exec_in_client():
    from app import proxmox
    assert not hasattr(proxmox.ProxmoxClient, "qemu_apt_update_exec")
    assert not hasattr(proxmox.ProxmoxClient, "_post")


def test_guest_detail_lxc_and_stopped_config(tmp_path, monkeypatch):
    c = _full_app(tmp_path, monkeypatch)
    html = c.get("/guests/101").get_data(as_text=True)
    assert "VM-Konfiguration (qm config)" in html and "nicht unterstützt" in html
    assert "pw123" not in html and "Betriebssystem" not in html and "Letztes apt-Update" not in html
    c = _full_app(tmp_path, monkeypatch)
    from app import proxmox
    monkeypatch.setattr(proxmox.ProxmoxClient, "guests", lambda s: [
        {"node": "n1", "vmid": 100, "name": "web", "type": "qemu", "status": "stopped"}])
    html = c.get("/guests/100").get_data(as_text=True)
    assert "Gast läuft nicht." in html and "VM-Konfiguration (qm config)" in html
    assert "Betriebssystem" not in html and "s3cret" not in html


def test_apt_update_parsing_and_todo(tmp_path):
    from datetime import datetime, timezone
    from app.aptmonitor import create_apt_todos, parse_timestamp
    from app.kanban import TodoStore
    assert parse_timestamp("2024-05-01 10:11:12 +0200").utcoffset().total_seconds() == 7200
    assert parse_timestamp("2024-05-01T10:11:12Z").tzinfo is not None
    assert parse_timestamp("2024-05-01T10:11:12+02:00").utcoffset().total_seconds() == 7200
    assert parse_timestamp("2024-05-01 10:11:12").utcoffset().total_seconds() == 0
    assert parse_timestamp("müll") is None

    class Client:
        def qemu_file_read(self, node, vmid, path):
            stamp = {1: "2024-01-01 00:00:00 +0000", 2: "2024-05-20 00:00:00 +0000"}.get(vmid)
            return {"content": f"foo: bar\napt-update: {stamp}\n"} if stamp else {"content": "x"}

    guests = [{"vmid": v, "name": f"vm{v}", "node": "n", "type": "qemu", "status": "running"} for v in (1, 2, 3)]
    guests.append({"vmid": 4, "type": "lxc", "status": "running"})
    guests.append({"vmid": 1, "type": "qemu", "status": "stopped"})
    store = TodoStore(str(tmp_path / "t.json"))
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert len(create_apt_todos(Client(), guests, store, 30, now)) == 1
    assert create_apt_todos(Client(), guests, store, 30, now) == []
    (todo,) = store.all()
    assert todo["title"].startswith("apt-Update überfällig") and todo["status"] == "planned"
    assert todo["history"][0]["user"] == "apt-monitor" and todo["history"][0]["comment"]


def test_cpubar_filter():
    app = create_app({"SECRET_KEY": "x" * 32, "TESTING": True,
                      "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    f = app.jinja_env.filters["cpubar"]
    assert "width:42%" in f(0.42) and "CPU 42 %" in f(0.42)
    assert "high" in f(0.95) and "width:100%" in f(5)
    assert "na" in f(None) and "na" in f("x")


def test_guests_page_cpu_bar(client, monkeypatch):
    from app import proxmox
    monkeypatch.setattr(proxmox.ProxmoxClient, "nodes", lambda s: [{"node": "n1", "status": "online", "cpu": 0.1}])
    monkeypatch.setattr(proxmox.ProxmoxClient, "guests",
                        lambda s: [{"node": "n1", "vmid": 100, "name": "web", "type": "qemu",
                                    "status": "running", "cpu": 0.5}])
    client.post("/login", data={"username": "admin", "password": "pw", "csrf": token(client)})
    html = client.get("/guests").get_data(as_text=True)
    assert "cpubar-fill" in html and "width:50%" in html
