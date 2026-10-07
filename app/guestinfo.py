"""Hilfsfunktionen für Gast-Details: IP-Extraktion, Port-Check, Kommentar-Speicher, host.info."""
import ipaddress
import json
from datetime import datetime
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
DEFAULT_SERVICE_PORTS = "22:ssh,80:http,443:https"


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


def parse_required_services(spec):
    """'100:ssh,http;101:postgres;*:ssh' -> {'100': ['ssh', 'http'], ...}; '*' als VMID gilt für alle Gäste,
    '*'/'all' als Dienst für alle in SERVICE_PORTS konfigurierten Dienste."""
    required = {}
    for part in (spec or "").split(";"):
        vmid, sep, names = part.strip().partition(":")
        vmid = vmid.strip()
        if not sep or not (vmid == "*" or vmid.isdigit()):
            continue
        labels = [n.strip() for n in names.split(",") if n.strip()]
        if labels:
            required.setdefault(vmid, []).extend(labels)
    return {k: _dedupe(v) for k, v in required.items()}


def required_services_for(vmid, required, ports):
    """Benötigte Dienst-Labels für einen Gast (globale '*'-Einträge plus VMID-spezifische)."""
    labels = []
    for name in required.get("*", []) + required.get(str(vmid), []):
        if name.lower() in ("*", "all"):
            labels.extend(label for _, label in ports)
        else:
            labels.append(name)
    return _dedupe(labels)


def missing_services(vmid, required, ports, detected):
    """Benötigte Dienste, die nicht erkannt wurden. Dienste ohne Port in SERVICE_PORTS sind nicht prüfbar und werden ignoriert."""
    checkable = {label for _, label in ports}
    return [n for n in required_services_for(vmid, required, ports) if n in checkable and n not in detected]


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
