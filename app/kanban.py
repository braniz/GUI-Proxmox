"""Persistence and host information helpers for the Kanban board."""
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone


MAX_TODO_TITLE_LEN = 200
MAX_TODO_DESCRIPTION_LEN = 5000
MAX_HOST_INFO_BYTES = 64 * 1024
MAX_COMMENT_LEN = 1000
TODO_STATUSES = {"planned", "in_progress", "done"}
BULK_ACTIONS = {"move", "edit", "done", "delete"}


class TodoStore:
    """Thread-safe JSON storage for Kanban todos."""

    _lock = threading.Lock()

    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def all(self):
        with self._lock:
            return self._read()

    @property
    def audit_path(self):
        root, ext = os.path.splitext(self.path)
        return f"{root}-audit{ext or '.json'}"

    def audit_log(self):
        """Dauerhaftes Protokoll gelöschter ToDos (bleibt nach dem Löschen erhalten)."""
        with self._lock:
            try:
                with open(self.audit_path, encoding="utf-8") as f:
                    data = json.load(f)
                return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []
            except (OSError, ValueError):
                return []

    def _write(self, todos, path=None):
        path = path or self.path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(todos, f, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def create(self, todo):
        with self._lock:
            todos = self._read()
            todos.append(todo)
            self._write(todos)
        return todo

    def create_unique(self, todo, key):
        """Legt das ToDo nur an, wenn kein nicht erledigtes ToDo mit gleichem auto_key existiert."""
        with self._lock:
            todos = self._read()
            if any(t.get("auto_key") == key and t.get("status") != "done" for t in todos):
                return None
            todos.append({**todo, "auto_key": key})
            self._write(todos)
        return todo

    def close_auto(self, keys, comment):
        """Setzt offene Auto-ToDos mit passendem auto_key auf done und fügt einen Kommentar hinzu."""
        keys = set(keys)
        closed = []
        with self._lock:
            todos = self._read()
            for t in todos:
                if t.get("auto_key") in keys and t.get("status") != "done":
                    t["status"] = "done"
                    t.setdefault("comments", []).append(comment)
                    t.setdefault("history", []).append(make_history_entry(
                        "done", comment.get("text", ""), comment.get("author", "unbekannt")))
                    closed.append(t)
            if closed:
                self._write(todos)
        return closed

    def update(self, todo_id, changes, entry=None):
        with self._lock:
            todos = self._read()
            todo = next((item for item in todos if item.get("id") == todo_id), None)
            if todo is None:
                return None
            todo.update(changes)
            if entry is not None:
                todo.setdefault("history", []).append(dict(entry))
            self._write(todos)
            return todo

    def delete(self, todo_id, entry=None):
        with self._lock:
            todos = self._read()
            removed = [item for item in todos if item.get("id") == todo_id]
            if not removed:
                return False
            self._archive(removed, entry)
            self._write([item for item in todos if item.get("id") != todo_id])
            return True

    def _archive(self, removed, entry):
        """Hängt gelöschte ToDos samt Historie an das persistente Audit-Log an (Lock gehalten)."""
        try:
            with open(self.audit_path, encoding="utf-8") as f:
                log = json.load(f)
            if not isinstance(log, list):
                log = []
        except (OSError, ValueError):
            log = []
        for item in removed:
            record = dict(item)
            record["history"] = list(item.get("history") or []) + ([dict(entry)] if entry else [])
            record["deleted_at"] = entry["time"] if entry else make_history_entry("delete", "", "")["time"]
            log.append(record)
        self._write(log, self.audit_path)

    def bulk(self, ids, action, changes, entry):
        """Wendet eine Aktion auf alle ToDos an; ein Schreibvorgang, Ergebnis pro ID."""
        with self._lock:
            todos = self._read()
            by_id = {t.get("id"): t for t in todos}
            results, hit = [], []
            for todo_id in ids:
                todo = by_id.get(todo_id)
                if todo is None:
                    results.append({"id": todo_id, "ok": False, "error": "ToDo nicht gefunden."})
                    continue
                hit.append(todo)
                results.append({"id": todo_id, "ok": True})
            if action == "delete":
                if hit:
                    self._archive(hit, entry)
                    gone = {t["id"] for t in hit}
                    self._write([t for t in todos if t.get("id") not in gone])
            else:
                for todo in hit:
                    todo.update(changes)
                    todo.setdefault("history", []).append(dict(entry))
                if hit:
                    self._write(todos)
            return results


def make_history_entry(action, comment, user):
    """Server-seitig erzeugter Historieneintrag; Zeit und Benutzer kommen nie vom Client."""
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": user or "unbekannt",
        "action": action,
        "comment": comment,
    }


def validate_comment(data):
    """Pflichtkommentar prüfen und normalisiert zurückgeben oder ValueError auslösen."""
    comment = data.get("comment") if isinstance(data, dict) else None
    if not isinstance(comment, str) or not comment.strip():
        raise ValueError("Ein Kommentar ist erforderlich.")
    if len(comment.strip()) > MAX_COMMENT_LEN:
        raise ValueError(f"Der Kommentar darf höchstens {MAX_COMMENT_LEN} Zeichen lang sein.")
    return comment.strip()


def validate_todo_fields(data, require_title=False):
    """Validate mutable fields and return normalized values or raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("Ungültige Eingabe.")
    changes = {}
    if "title" in data or require_title:
        title = data.get("title", "")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TODO_TITLE_LEN:
            raise ValueError(f"Der Titel muss 1 bis {MAX_TODO_TITLE_LEN} Zeichen lang sein.")
        changes["title"] = title.strip()
    if "description" in data:
        description = data["description"]
        if not isinstance(description, str) or len(description) > MAX_TODO_DESCRIPTION_LEN:
            raise ValueError(f"Die Beschreibung darf höchstens {MAX_TODO_DESCRIPTION_LEN} Zeichen lang sein.")
        changes["description"] = description.strip()
    if "status" in data:
        status = data["status"]
        if not isinstance(status, str) or status not in TODO_STATUSES:
            raise ValueError("Ungültiger Status.")
        changes["status"] = status
    if "vmid" in data:
        vmid = data["vmid"]
        if vmid in (None, ""):
            changes["vmid"] = None
        elif isinstance(vmid, (str, int)) and re.fullmatch(r"\d{1,10}", str(vmid)):
            changes["vmid"] = str(vmid)
        else:
            raise ValueError("Ungültige VMID.")
    return changes


def read_host_info(path, limit=MAX_HOST_INFO_BYTES):
    """Read a bounded host.info file; missing or unreadable files are optional."""
    try:
        with open(path, "rb") as f:
            content = f.read(limit + 1)
    except OSError:
        return None
    return content[:limit].decode("utf-8", "replace") if content else None
