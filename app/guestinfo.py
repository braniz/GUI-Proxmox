"""Hilfsfunktionen für Gast-Details: IP-Extraktion, Port-Check, Kommentar-Speicher, host.info."""
import base64
import binascii
import ipaddress
import json
from datetime import datetime, timezone
import os
import re
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_COMMENT_LEN = 2000
MAX_HOST_INFO_BYTES = 8 * 1024
HOST_INFO_PATH = "/srv/info/host.info"
OS_RELEASE_PATH = "/etc/os-release"
REBOOT_REQUIRED_PATH = "/var/run/reboot-required"
UPDATES_AVAILABLE_PATH = "/var/lib/update-notifier/updates-available"
DEBIAN_LIKE = {"debian", "ubuntu", "linuxmint", "raspbian", "pop"}
DEFAULT_SERVICE_PORTS = "22:ssh,80:http,443:https,21:ftp,25:smtp,53:dns,445:smb,3306:mysql,3389:rdp,5432:postgres"


def _usable_ip(value):
    """Gibt die IP als String zurück, außer bei Loopback/link-local/ungültig."""
    try:
        ip = ipaddress.ip_address(str(value).split("/")[0].strip())
    except ValueError:
        return None
    if ip.is_loopback or ip.is_link_local:
        return None
    return str(ip)


def _dedupe(items):
    return list(dict.fromkeys(items))


def extract_qemu_ips(data):
    """IPs aus agent/network-get-interfaces (Antwort mit oder ohne 'result'-Wrapper)."""
    if isinstance(data, dict):
        data = data.get("result")
    ips = []
    for iface in data if isinstance(data, list) else []:
        if not isinstance(iface, dict) or iface.get("name") == "lo":
            continue
        for addr in iface.get("ip-addresses") or []:
            ip = _usable_ip(addr.get("ip-address")) if isinstance(addr, dict) else None
            if ip:
                ips.append(ip)
    return _dedupe(ips)


def extract_lxc_ips(data):
    """IPs aus lxc/{vmid}/interfaces (Felder 'inet'/'inet6' mit CIDR)."""
    ips = []
    for iface in data if isinstance(data, list) else []:
        if not isinstance(iface, dict) or iface.get("name") == "lo":
            continue
        for key in ("inet", "inet6"):
            ip = _usable_ip(iface.get(key)) if iface.get(key) else None
            if ip:
                ips.append(ip)
    return _dedupe(ips)


def parse_service_ports(spec):
    """Unterstützt sowohl '22:ssh' als auch 'ssh:22' und liefert [(22, 'ssh')] zurück."""
    ports = []
    for part in (spec or "").split(","):
        left, _, right = part.strip().partition(":")
        left, right = left.strip(), right.strip()
        if not left:
            continue

        # Format A: port:label (z. B. 22:ssh)
        if left.isdigit() and 0 < int(left) < 65536:
            label = right or left
            ports.append((int(left), label))
            continue

        # Format B: label:port (z. B. ssh:22)
        if right.isdigit() and 0 < int(right) < 65536:
            label = left or right
            ports.append((int(right), label))
            continue
    return ports


def missing_services(selected, ports, detected):
    """Ausgewählte (extern überwachte) Dienste, die nicht erreichbar sind. Unbekannte Dienste werden ignoriert."""
    checkable = {label for _, label in ports}
    return [n for n in _dedupe(selected or []) if n in checkable and n not in detected]


def check_port(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def detect_services(ip, ports, timeout=0.5, checker=check_port):
    """Parallele TCP-Connect-Checks; Rückgabe: Labels der offenen Ports."""
    if not ip or not ports:
        return []
    with ThreadPoolExecutor(max_workers=len(ports)) as pool:
        results = list(pool.map(lambda p: checker(ip, p[0], timeout), ports))
    return [label for (_, label), ok in zip(ports, results) if ok]


def clean_host_info(data, limit=MAX_HOST_INFO_BYTES):
    """Liest 'content' aus file-read-Antwort; None wenn leer/ungültig. Escaping übernimmt Jinja."""
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        return None
    raw = content.encode("utf-8", "replace")[:limit]
    return raw.decode("utf-8", "replace")


def parse_os_ids(data):
    """Menge aus ID/ID_LIKE einer os-release-Antwort (file-read); leer wenn unbekannt."""
    content = data.get("content") if isinstance(data, dict) else None
    ids = set()
    for line in content.splitlines() if isinstance(content, str) else []:
        key, _, val = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids.update(val.strip().strip("\"'").lower().split())
    return ids


def parse_updates_count(data):
    """Anzahl aus /var/lib/update-notifier/updates-available ("N updates can be applied ..."); None wenn unklar."""
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        return None
    for line in content.splitlines():
        head = line.strip().split(" ", 1)[0]
        if head.isdigit() and "update" in line:
            return int(head)
    return 0 if "0 updates" in content or "0 Aktualisierungen" in content else None


def update_status(read_file):
    """Ermittelt (updates_available, reboot_required) mit Werten True/False/None (unbekannt).

    read_file(path) liest per Guest Agent (file-read, nur lesend) und wirft bei Fehlern
    ProxmoxError/ValueError/TypeError. Unterstützt: Debian-artige Linux-Gäste; sonst unbekannt.
    """
    try:
        ids = parse_os_ids(read_file(OS_RELEASE_PATH))
    except Exception:
        return None, None
    if not ids & DEBIAN_LIKE:
        return None, None
    try:
        read_file(REBOOT_REQUIRED_PATH)
        reboot = True
    except Exception:
        reboot = False
    try:
        count = parse_updates_count(read_file(UPDATES_AVAILABLE_PATH))
    except Exception:
        count = None
    return (None if count is None else count > 0), reboot


def apply_comment_prefix(old, submitted, user, now=None):
    """Stellt neuen Kommentarzeilen "[Datum Zeit] User: " voran; neue Einträge stehen oben, gespeicherte Zeilen bleiben unverändert."""
    old_lines = (old or "").replace("\r\n", "\n").split("\n")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    name = user or "unbekannt"
    prefix = f"[{stamp}] {name}: "
    own_header = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] " + re.escape(name) + r":(?: |$)")
    new_out, old_out, in_new = [], [], False
    for line in (submitted or "").replace("\r\n", "\n").strip().split("\n"):
        line = line.rstrip()
        if line in old_lines and line:
            old_lines.remove(line)
            old_out.append(line)
            in_new = False
            continue
        if not line:
            (new_out if in_new else old_out).append(line)
            continue
        m = own_header.match(line)
        if m:
            line = line[m.end():]
            if not line:
                in_new = True
                new_out.append(None)
                continue
            new_out.append(prefix + line)
        elif in_new and new_out and new_out[-1] is None:
            new_out[-1] = prefix + line
        else:
            new_out.append(line if in_new else prefix + line)
        in_new = True
    new_out = [x for x in new_out if x is not None]
    return "\n".join(new_out + old_out).strip()


class CommentStore:
    """Einfache JSON-Datei: {vmid: kommentar}."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def all(self):
        with self._lock:
            return self._read()

    def get(self, vmid):
        return self.all().get(str(vmid), "")

    def set(self, vmid, text):
        text = (text or "").strip()
        if len(text) > MAX_COMMENT_LEN:
            raise ValueError("Kommentar zu lang")
        with self._lock:
            data = self._read()
            if text:
                data[str(vmid)] = text
            else:
                data.pop(str(vmid), None)
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise


class ServiceMonitorStore:
    """JSON-Datei: {vmid: [dienst, ...]} mit den extern zu überwachenden Diensten pro Gast."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): [x for x in v if isinstance(x, str)]
                for k, v in data.items() if isinstance(v, list)}

    def all(self):
        with self._lock:
            return self._read()

    def set_many(self, selections):
        """Ersetzt die Auswahl der übergebenen VMIDs; leere Auswahl entfernt den Eintrag."""
        with self._lock:
            data = self._read()
            for vmid, labels in selections.items():
                if labels:
                    data[str(vmid)] = _dedupe(labels)
                else:
                    data.pop(str(vmid), None)
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise


PSEUDO_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2",
             "devpts", "efivarfs", "securityfs", "debugfs", "tracefs", "configfs", "fusectl", "mqueue"}


def _result(data):
    return data.get("result") if isinstance(data, dict) and "result" in data else data


def extract_qemu_hostname(data):
    data = _result(data)
    name = data.get("host-name") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def extract_qemu_interfaces(data):
    """Interfaces mit Name, MAC und IPs (ohne 'lo')."""
    out = []
    data = _result(data)
    for iface in data if isinstance(data, list) else []:
        if not isinstance(iface, dict) or iface.get("name") == "lo":
            continue
        ips = extract_qemu_ips([iface])
        mac = iface.get("hardware-address")
        out.append({"name": str(iface.get("name") or ""), "mac": mac if isinstance(mac, str) else None,
                    "ips": ips})
    return out


def human_size(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def extract_fsinfo(data):
    out = []
    data = _result(data)
    for fs in data if isinstance(data, list) else []:
        if not isinstance(fs, dict):
            continue
        ftype = str(fs.get("type") or "")
        if ftype.lower() in PSEUDO_FS:
            continue
        used, total = fs.get("used-bytes"), fs.get("total-bytes")
        ok = (isinstance(used, int) and isinstance(total, int)
              and not isinstance(used, bool) and not isinstance(total, bool) and total > 0)
        out.append({"mount": str(fs.get("mountpoint") or fs.get("name") or ""), "type": ftype,
                    "used": human_size(used) if ok else None,
                    "total": human_size(total) if ok else None,
                    "percent": round(used * 100 / total, 1) if ok else None})
    return out


def parse_loadavg(data):
    """(1, 5, 15 Min) aus Inhalt von /proc/loadavg, sonst None."""
    content = data.get("content") if isinstance(data, dict) else None
    parts = content.split() if isinstance(content, str) else []
    try:
        return tuple(float(x) for x in parts[:3]) if len(parts) >= 3 else None
    except ValueError:
        return None


def count_cpus(data):
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        return None
    n = sum(1 for line in content.splitlines() if re.match(r"processor\s*:", line))
    return n or None


OSINFO_FIELDS = (("name", "Name"), ("pretty-name", "Bezeichnung"), ("version", "Version"),
                 ("kernel-release", "Kernel-Release"), ("kernel-version", "Kernel-Version"),
                 ("machine", "Architektur"), ("id", "ID"))
SENSITIVE_CONFIG = re.compile(r"password|passwd|sshkeys|secret|token", re.I)
_STAT_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def extract_osinfo(data):
    data = _result(data)
    if not isinstance(data, dict):
        return None
    out = [(label, str(data[key])) for key, label in OSINFO_FIELDS if data.get(key) not in (None, "")]
    return out or None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def format_epoch(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def extract_users(data):
    out = []
    data = _result(data)
    for u in data if isinstance(data, list) else []:
        if not isinstance(u, dict):
            continue
        login = u.get("login-time")
        try:
            when = format_epoch(login) if _num(login) else None
        except (OverflowError, OSError, ValueError):
            when = None
        name = str(u.get("user") or "")
        if u.get("domain"):
            name = f"{u['domain']}\\{name}"
        out.append({"user": name, "login": when})
    return out


def parse_guest_time(data, now=None):
    """Gastzeit (ns seit Epoch) -> {'utc','local','diff'}; diff = Gast minus Server in Sekunden."""
    ns = _result(data)
    if not _num(ns):
        return None
    secs = ns / 1e9
    now = now if now is not None else datetime.now(timezone.utc)
    return {"utc": format_epoch(secs),
            "local": datetime.fromtimestamp(secs, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "diff": round(secs - now.timestamp(), 1)}


def parse_fsfreeze_status(data):
    data = _result(data)
    return data if data in ("thawed", "frozen") else None


def mask_config(cfg):
    """Sortierte (Schlüssel, Wert)-Liste; sensible Werte werden maskiert."""
    if not isinstance(cfg, dict):
        return None
    return [(str(k), "***" if SENSITIVE_CONFIG.search(str(k)) else str(v)) for k, v in sorted(cfg.items())]


def parse_apt_timestamp(data):
    """Zeitstempel aus exec-status von `stat -c %y` ('out-data' im Klartext oder base64); None wenn unklar."""
    if not isinstance(data, dict) or data.get("exitcode") not in (0, None):
        return None
    out = data.get("out-data")
    if not isinstance(out, str):
        return None
    out = out.strip()
    if not _STAT_TS.match(out):
        try:
            out = base64.b64decode(out, validate=True).decode("utf-8", "replace").strip()
        except (binascii.Error, ValueError):
            return None
    return out[:40] if _STAT_TS.match(out) else None
