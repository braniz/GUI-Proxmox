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
    for path in ("/", "/nodes", "/guests", "/storage"):
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
