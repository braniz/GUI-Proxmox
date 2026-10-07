"""Hilfsfunktionen für Gast-Details: IP-Extraktion, Port-Check, Kommentar-Speicher, host.info."""
import ipaddress
import json
from datetime import datetime
import os
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_COMMENT_LEN = 2000
MAX_HOST_INFO_BYTES = 8 * 1024
HOST_INFO_PATH = "/srv/info/host.info"
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
    """'22:ssh,80:http' -> [(22, 'ssh'), (80, 'http')]; ungültige Einträge werden ignoriert."""
    ports = []
    for part in (spec or "").split(","):
        num, _, label = part.strip().partition(":")
        if num.isdigit() and 0 < int(num) < 65536:
            ports.append((int(num), label.strip() or num))
    return ports


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


def apply_comment_prefix(old, submitted, user, now=None):
    """Stellt neuen Kommentarzeilen "[Datum Zeit] User: " voran; bereits gespeicherte Zeilen bleiben unverändert."""
    old_lines = (old or "").replace("\r\n", "\n").split("\n")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    prefix = f"[{stamp}] {user or 'unbekannt'}: "
    out, in_new = [], False
    for line in (submitted or "").replace("\r\n", "\n").strip().split("\n"):
        line = line.rstrip()
        if line in old_lines and line:
            old_lines.remove(line)
            out.append(line)
            in_new = False
        elif not line:
            out.append(line)
        else:
            out.append(line if in_new else prefix + line)
            in_new = True
    return "\n".join(out).strip()


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
