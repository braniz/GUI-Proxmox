"""Proxmox-Client: ausschließlich lesende GET-Anfragen."""
from urllib.parse import quote

import requests


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
