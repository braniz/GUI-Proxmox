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
    for path in ("/", "/nodes", "/guests", "/storage", "/status"):
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
    assert CommentStore(str(tmp_path / "c.json")).get(100) == "<b>hi</b>"


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
