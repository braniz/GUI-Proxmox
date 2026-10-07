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
TODO_STATUSES = {"planned", "in_progress", "done"}


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

    def _write(self, todos):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(todos, f, ensure_ascii=False)
            os.replace(tmp, self.path)
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

    def update(self, todo_id, changes):
        with self._lock:
            todos = self._read()
            todo = next((item for item in todos if item.get("id") == todo_id), None)
            if todo is None:
                return None
            todo.update(changes)
            self._write(todos)
            return todo

    def delete(self, todo_id):
        with self._lock:
            todos = self._read()
            remaining = [item for item in todos if item.get("id") != todo_id]
            if len(remaining) == len(todos):
                return False
            self._write(remaining)
            return True


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
