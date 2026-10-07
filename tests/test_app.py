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
    assert not re.search(r"requests\.(post|put|delete|patch)", src)


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
    ports = parse_service_ports("22:ssh,80:http,x:bad,443")
    assert ports == [(22, "ssh"), (80, "http"), (443, "443")]
    assert detect_services("1.2.3.4", ports, checker=lambda ip, p, t: p == 22) == ["ssh"]
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
                      "SERVICE_CHECK": False, "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    P = proxmox.ProxmoxClient
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
    assert c.put(f"/api/kanban/todos/{todo['id']}", json={"status": "done"},
                 headers=headers).get_json()["status"] == "done"
    assert c.put(f"/api/kanban/todos/{todo['id']}", json={"status": "invalid"},
                 headers=headers).status_code == 400
    assert c.delete(f"/api/kanban/todos/{todo['id']}", headers=headers).status_code == 204
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
                      "SERVICE_CHECK": False, "ADMIN_PASSWORD_HASH": generate_password_hash("pw")})
    c = app.test_client()
    P = proxmox.ProxmoxClient
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


def test_auto_todo_for_missing_required_service(tmp_path, monkeypatch):
    from app import guestinfo
    from app.guestinfo import parse_required_services, missing_services
    ports = guestinfo.parse_service_ports("22:ssh,80:http,5432:postgres")
    req = parse_required_services("100:ssh,postgres;*:http;x:y;101:all")
    assert req == {"100": ["ssh", "postgres"], "*": ["http"], "101": ["all"]}
    assert missing_services("100", req, ports, ["ssh"]) == ["http", "postgres"]
    assert missing_services("101", req, ports, ["ssh"]) == ["http", "postgres"]
    assert missing_services("5", {}, ports, []) == []

    import app as app_module
    app = create_app({
        "SECRET_KEY": "x" * 32, "TESTING": True,
        "ADMIN_PASSWORD_HASH": generate_password_hash("pw"),
        "AUTO_TODO_ENABLED": True, "REQUIRED_SERVICES": "100:ssh,http",
        "SERVICE_PORTS": "22:ssh,80:http", "KANBAN_TODOS_DB": str(tmp_path / "t.json"),
    })

    class FakeClient:
        def guests(self):
            return [{"vmid": 100, "name": "web", "type": "qemu", "node": "n", "status": "running"},
                    {"vmid": 100 + 1, "name": "off", "type": "qemu", "node": "n", "status": "stopped"}]
        def qemu_interfaces(self, node, vmid):
            return [{"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.5"}]}]
        def qemu_file_read(self, *a):
            raise app_module.ProxmoxError("x")
    monkeypatch.setattr(app_module, "ProxmoxClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(app_module, "detect_services", lambda ip, ports: ["ssh"])
    monkeypatch.setattr(app_module, "update_status", lambda f: (None, None))
    c = app.test_client()
    _login(c)
    for _ in range(2):
        assert c.get("/kanban").status_code == 200
    todos = c.get("/api/kanban/todos").get_json()
    assert len(todos) == 1
    assert todos[0]["status"] == "planned" and todos[0]["vmid"] == "100"
    assert "http" in todos[0]["title"]
