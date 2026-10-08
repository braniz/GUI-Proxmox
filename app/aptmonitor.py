"""Überwachung des letzten apt-Updates von QEMU-Gästen; erzeugt Kanban-ToDos bei Überfälligkeit."""
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .guestinfo import APT_INFO_PATH, APT_UPDATE_PREFIX, format_stat_time
from .kanban import make_history_entry
from .proxmox import ProxmoxError

DEFAULT_MAX_AGE_DAYS = 30
APT_MONITOR_USER = "apt-monitor"

_STAT_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_apt_update_datetime(data):
    """Zeitstempel der 'apt-update:'-Zeile als tz-aware datetime (ohne Zeitzone = UTC); sonst None."""
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        return None
    for line in content.splitlines():
        line = line.strip()
        if line.lower().startswith(APT_UPDATE_PREFIX):
            return parse_timestamp(line[len(APT_UPDATE_PREFIX):])
    return None


def parse_timestamp(text):
    """'2024-05-01 10:11:12 +0200', ISO 8601 ('...Z', '...+02:00') oder ohne Zeitzone (UTC)."""
    text = (text or "").strip()
    if not text:
        return None
    m = _STAT_RE.match(text)
    try:
        if m:
            y, mo, d, t, tz = m.groups()
            tz = "+0000" if tz in (None, "Z") else tz.replace(":", "")
            return datetime.strptime(f"{y}-{mo}-{d} {t} {tz}", "%Y-%m-%d %H:%M:%S %z")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def apt_age_days(updated, now=None):
    """Alter in Tagen (float) seit dem Update."""
    now = now or datetime.now(timezone.utc)
    return (now - updated) / timedelta(days=1)


def parse_max_age(value, default=DEFAULT_MAX_AGE_DAYS):
    try:
        days = float(value)
    except (TypeError, ValueError):
        return default
    return days if days >= 0 else default


def create_apt_todos(client, guests, store, max_age_days=DEFAULT_MAX_AGE_DAYS, now=None):
    """Legt für laufende QEMU-Gäste mit überfälligem apt-Update je ein ToDo (planned) an.

    Pro Gast nur ein offenes ToDo (auto_key 'apt-update:<vmid>'). Gibt die neu angelegten ToDos zurück."""
    now = now or datetime.now(timezone.utc)
    created = []
    for g in guests:
        if g.get("type") != "qemu" or g.get("status") != "running":
            continue
        vmid = g.get("vmid")
        try:
            updated = parse_apt_update_datetime(client.qemu_file_read(g.get("node"), vmid, APT_INFO_PATH))
        except (ProxmoxError, ValueError, TypeError):
            continue
        if updated is None:
            continue
        age = apt_age_days(updated, now)
        if age <= max_age_days:
            continue
        name = g.get("name") or vmid
        last = format_stat_time(updated.strftime("%Y-%m-%d %H:%M:%S %z"))
        comment = (f"Letztes apt-Update am {last}, {int(age)} Tage her "
                   f"(Schwellwert: {max_age_days:g} Tage).")
        todo = {
            "id": uuid4().hex,
            "title": f"apt-Update überfällig: {name} ({vmid})",
            "description": f"Auf {name} (VMID {vmid}) liegt das letzte apt-Update länger als "
                           f"{max_age_days:g} Tage zurück. {comment}",
            "status": "planned",
            "vmid": str(vmid),
            "created_at": now.isoformat(timespec="seconds"),
            "history": [make_history_entry("create", comment, APT_MONITOR_USER)],
        }
        try:
            if store.create_unique(todo, f"apt-update:{vmid}"):
                created.append(todo)
        except OSError:
            pass
    return created
