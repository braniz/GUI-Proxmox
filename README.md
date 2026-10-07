# GUI-Proxmox

Proxmox Projekt für die Webbasierte Darstellung und Management von Proxmox Clustern.

## Read-only Benutzer für Monitoring

Als erstes benötigst du einen Proxmox-User mit nur lesenden Rechten. Dafür kann folgender Benutzer angelegt werden:

```bash
pveum user add monitor-user@pve -comment "Technischer Lesebenutzer für Monitoring"
pveum passwd monitor-user@pve
pveum acl modify / -user monitor-user@pve -role PVEAuditor
pveum user token add monitor-user@pve monitoring-token -privsep 0
```

### Erklärung

- `pveum user add monitor-user@pve -comment "Technischer Lesebenutzer für Monitoring"` legt den technischen Benutzer an.
- `pveum passwd monitor-user@pve` setzt oder ändert das Passwort des Benutzers.
- `pveum acl modify / -user monitor-user@pve -role PVEAuditor` weist dem Benutzer die schreibgeschützte Rolle `PVEAuditor` auf der gesamten Proxmox-Umgebung zu.
- `pveum user token add monitor-user@pve monitoring-token -privsep 0` erstellt einen API-Token ohne separate Rechteprüfung, der für Monitoring oder die GUI verwendet werden kann.

## Cluster auslesen (API-Token)

`proxmox_cluster.py` liest den Cluster rein lesend (nur GET) mit dem API-Token aus. Es werden nur Python 3 und die Standardbibliothek benötigt.

```bash
export PROXMOX_HOST=pve.example.local
export PROXMOX_TOKEN_ID='monitor-user@pve!monitoring-token'
export PROXMOX_TOKEN_SECRET='<secret aus pveum user token add>'
# optional: PROXMOX_PORT (8006), PROXMOX_VERIFY_SSL=false (selbstsignierte Zertifikate)
python3 proxmox_cluster.py
```

Die Ausgabe ist JSON mit Clustername, Quorum-Status und Nodes. In Python:

```python
from proxmox_cluster import ProxmoxClient
client = ProxmoxClient.from_env()
client.summary()    # Zusammenfassung
client.resources()  # VMs, Container, Storage
```

Das Token-Secret niemals ins Repository committen (siehe `.env.example`). Tests: `python3 -m unittest`.
