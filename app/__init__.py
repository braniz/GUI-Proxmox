import hmac
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

from .proxmox import ProxmoxClient, ProxmoxError

# Hash zum Angleichen der Laufzeit bei unbekanntem Benutzer
_DUMMY_HASH = "scrypt:32768:8:1$dummy$" + "0" * 128


def _verify_setting(value):
    v = (value or "true").strip()
    if v.lower() in ("true", "1", "yes"):
        return True
    if v.lower() in ("false", "0", "no"):
        return False
    return v  # CA-Pfad


def group_guests_by_node(nodes, guests):
    """Gruppiert Gäste pro Node (Baum: Node -> VMs/LXC), sortiert nach VMID."""
    tree = {}
    for n in nodes or []:
        tree[n.get("node")] = {"info": n, "guests": []}
    for g in guests or []:
        name = g.get("node")
        tree.setdefault(name, {"info": {"node": name, "status": "unknown"}, "guests": []})
        tree[name]["guests"].append(g)
    for entry in tree.values():
        entry["guests"].sort(key=lambda g: g.get("vmid") or 0)
    return [dict(name=k, **v) for k, v in sorted(tree.items(), key=lambda kv: str(kv[0]))]


def create_app(config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
        ADMIN_USER=os.environ.get("ADMIN_USER", "admin"),
        ADMIN_PASSWORD_HASH=os.environ.get("ADMIN_PASSWORD_HASH", ""),
        PVE_HOST=os.environ.get("PVE_HOST", ""),
        PVE_PORT=os.environ.get("PVE_PORT", "8006"),
        PVE_TOKEN_ID=os.environ.get("PVE_TOKEN_ID", ""),
        PVE_TOKEN_SECRET=os.environ.get("PVE_TOKEN_SECRET", ""),
        PVE_VERIFY_SSL=_verify_setting(os.environ.get("PVE_VERIFY_SSL")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=3600,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == "change-me":
        raise RuntimeError("SECRET_KEY muss gesetzt werden (siehe .env.example).")
    if not app.config["ADMIN_PASSWORD_HASH"]:
        raise RuntimeError("ADMIN_PASSWORD_HASH muss gesetzt werden (python generate_hash.py).")

    def client():
        c = app.config
        return ProxmoxClient(c["PVE_HOST"], c["PVE_PORT"], c["PVE_TOKEN_ID"],
                             c["PVE_TOKEN_SECRET"], c["PVE_VERIFY_SSL"])

    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if not session.get("user"):
                return redirect(url_for("login", next=request.path))
            return view(*a, **kw)
        return wrapped

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    def check_csrf():
        sent = request.form.get("csrf", "")
        expected = session.get("csrf")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400)

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            check_csrf()
            user = request.form.get("username", "")
            pw = request.form.get("password", "")
            user_ok = hmac.compare_digest(user.encode(), app.config["ADMIN_USER"].encode())
            hash_ = app.config["ADMIN_PASSWORD_HASH"] if user_ok else _DUMMY_HASH
            try:
                pw_ok = check_password_hash(hash_, pw)
            except ValueError:
                pw_ok = False
            if user_ok and pw_ok:
                session.clear()
                session["user"] = user
                session.permanent = True
                nxt = request.args.get("next", "")
                if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
                    nxt = url_for("overview")
                return redirect(nxt)
            flash("Benutzername oder Passwort falsch.")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        check_csrf()
        session.clear()
        return redirect(url_for("login"))

    def page(template, **fetchers):
        data, error = {}, None
        try:
            for key, fn in fetchers.items():
                data[key] = fn()
        except ProxmoxError as exc:
            error = str(exc)
        return render_template(template, error=error, **data)

    @app.route("/")
    @login_required
    def overview():
        return page("overview.html", status=lambda: client().cluster_status(),
                    nodes=lambda: client().nodes())

    @app.route("/nodes")
    @login_required
    def nodes():
        return page("nodes.html", nodes=lambda: client().nodes())

    @app.route("/guests")
    @login_required
    def guests():
        def tree():
            c = client()
            return group_guests_by_node(c.nodes(), c.guests())
        return page("guests.html", tree=tree)

    @app.route("/storage")
    @login_required
    def storage():
        return page("storage.html", storage=lambda: client().storage())

    @app.template_filter("gib")
    def gib(v):
        try:
            return f"{float(v) / 1024**3:.1f} GiB"
        except Exception:
            return "-"

    @app.template_filter("pct")
    def pct(v):
        try:
            return f"{float(v) * 100:.0f} %"
        except Exception:
            return "-"

    return app
