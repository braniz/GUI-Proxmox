"""Read-only Zugriff auf einen Proxmox-Cluster per API-Token (nur Standardbibliothek)."""
import json
import os
import ssl
import sys
import urllib.request


class ProxmoxClient:
    def __init__(self, host, token_id, token_secret, port=8006, verify_ssl=True, timeout=10):
        self.base_url = f"https://{host}:{port}/api2/json"
        self.timeout = timeout
        self._headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._ctx = ssl.create_default_context()
        if not verify_ssl:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    @classmethod
    def from_env(cls):
        try:
            host = os.environ["PROXMOX_HOST"]
            token_id = os.environ["PROXMOX_TOKEN_ID"]
            token_secret = os.environ["PROXMOX_TOKEN_SECRET"]
        except KeyError as e:
            raise SystemExit(f"Umgebungsvariable {e.args[0]} fehlt.")
        return cls(
            host,
            token_id,
            token_secret,
            port=int(os.environ.get("PROXMOX_PORT", "8006")),
            verify_ssl=os.environ.get("PROXMOX_VERIFY_SSL", "true").lower() != "false",
        )

    def get(self, path):
        """Führt ausschliesslich GET-Anfragen aus und liefert das 'data'-Feld."""
        req = urllib.request.Request(self.base_url + path, headers=self._headers, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
            return json.load(resp)["data"]

    def cluster_status(self):
        return self.get("/cluster/status")

    def nodes(self):
        return self.get("/nodes")

    def resources(self):
        return self.get("/cluster/resources")

    def summary(self):
        status = self.cluster_status()
        cluster = next((s for s in status if s.get("type") == "cluster"), {})
        return {
            "cluster_name": cluster.get("name"),
            "quorate": bool(cluster.get("quorate")),
            "nodes": [
                {
                    "name": n.get("node"),
                    "status": n.get("status"),
                    "cpu": n.get("cpu"),
                    "mem": n.get("mem"),
                    "maxmem": n.get("maxmem"),
                    "uptime": n.get("uptime"),
                }
                for n in self.nodes()
            ],
        }


if __name__ == "__main__":
    json.dump(ProxmoxClient.from_env().summary(), sys.stdout, indent=2)
    print()
