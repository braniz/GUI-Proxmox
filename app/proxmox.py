"""Proxmox-Client: GET-Anfragen; einziger POST ist der fest verdrahtete Guest-Agent-Exec für das apt-Update."""
import time
from urllib.parse import quote

import requests


APT_UPDATE_COMMAND = ["stat", "-c", "%y", "/var/lib/apt/lists/"]
EXEC_TIMEOUT = 8


class ProxmoxError(Exception):
    pass


class ProxmoxPermissionError(ProxmoxError):
    """Proxmox antwortete mit 401/403."""


class ProxmoxClient:
    def __init__(self, host, port, token_id, token_secret, verify=True, timeout=10):
        self.base = f"https://{host}:{port}/api2/json"
        self.verify = verify
        self.timeout = timeout
        self.headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}

    def _get(self, path, params=None, timeout=None):
        try:
            r = requests.get(self.base + path, headers=self.headers, params=params,
                             verify=self.verify, timeout=timeout or self.timeout)
            r.raise_for_status()
            return r.json().get("data") or []
        except (requests.RequestException, ValueError) as exc:
            raise self._error(path, exc) from exc

    @staticmethod
    def _error(path, exc):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        cls = ProxmoxPermissionError if code in (401, 403) else ProxmoxError
        return cls(f"Proxmox-Abfrage fehlgeschlagen: {path} ({type(exc).__name__})")

    def _post(self, path, data=None, timeout=None):
        try:
            r = requests.post(self.base + path, headers=self.headers, data=data,
                              verify=self.verify, timeout=timeout or self.timeout)
            r.raise_for_status()
            return r.json().get("data") or {}
        except (requests.RequestException, ValueError) as exc:
            raise self._error(path, exc) from exc

    def cluster_status(self):
        return self._get("/cluster/status")

    def nodes(self):
        return self._get("/nodes")

    def resources(self, rtype=None):
        return self._get("/cluster/resources", {"type": rtype} if rtype else None)

    def guests(self):
        return [r for r in self.resources() if r.get("type") in ("qemu", "lxc")]

    def storage(self):
        return self.resources("storage")

    def node_version(self, node):
        return self._get(f"/nodes/{quote(str(node), safe='')}/version")

    # Gast-Details (nur GET). Kurze Timeouts, da pro Gast abgefragt wird.
    def qemu_interfaces(self, node, vmid):
        """Benötigt QEMU Guest Agent im Gast."""
        return self._get(f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/agent/network-get-interfaces",
                         timeout=3)

    def lxc_interfaces(self, node, vmid):
        return self._get(f"/nodes/{quote(str(node), safe='')}/lxc/{int(vmid)}/interfaces", timeout=3)

    def qemu_file_read(self, node, vmid, path):
        """Datei per Guest Agent lesen. Für LXC bietet die Proxmox-API keinen GET-Endpoint."""
        return self._get(f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/agent/file-read",
                         {"file": path}, timeout=3)

    def _qemu_agent(self, node, vmid, command):
        return self._get(f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/agent/{quote(command, safe='')}",
                         timeout=3)

    def qemu_hostname(self, node, vmid):
        return self._qemu_agent(node, vmid, "get-host-name")

    def qemu_fsinfo(self, node, vmid):
        return self._qemu_agent(node, vmid, "get-fsinfo")

    def qemu_status(self, node, vmid):
        return self._get(f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/status/current", timeout=3)

    def qemu_osinfo(self, node, vmid):
        return self._qemu_agent(node, vmid, "get-osinfo")

    def qemu_users(self, node, vmid):
        return self._qemu_agent(node, vmid, "get-users")

    def qemu_time(self, node, vmid):
        return self._qemu_agent(node, vmid, "get-time")

    def qemu_fsfreeze_status(self, node, vmid):
        """Nur Status lesen; freeze/thaw werden nie aufgerufen."""
        return self._qemu_agent(node, vmid, "fsfreeze-status")

    def qemu_config(self, node, vmid):
        return self._get(f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/config", timeout=3)

    def lxc_config(self, node, vmid):
        return self._get(f"/nodes/{quote(str(node), safe='')}/lxc/{int(vmid)}/config", timeout=3)

    def qemu_apt_update_exec(self, node, vmid, wait=EXEC_TIMEOUT):
        """Führt den fest verdrahteten Befehl APT_UPDATE_COMMAND per Guest Agent aus (keine Eingabe möglich)."""
        base = f"/nodes/{quote(str(node), safe='')}/qemu/{int(vmid)}/agent"
        pid = self._post(f"{base}/exec", {"command": APT_UPDATE_COMMAND}, timeout=3).get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise ProxmoxError("Proxmox-Abfrage fehlgeschlagen: exec (keine PID)")
        deadline = time.monotonic() + wait
        while True:
            st = self._get(f"{base}/exec-status", {"pid": pid}, timeout=3)
            if isinstance(st, dict) and st.get("exited"):
                return st
            if time.monotonic() >= deadline:
                raise ProxmoxError("Proxmox-Abfrage fehlgeschlagen: exec-status (Zeitüberschreitung)")
            time.sleep(0.3)
