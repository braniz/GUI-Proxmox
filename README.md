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

## Web-UI (read-only Proxmox-Viewer)

Passwortgeschützte Flask-Weboberfläche zur Anzeige von Cluster-Übersicht, Nodes, VMs/Containern und Storage. Die App führt ausschließlich `GET`-Anfragen gegen die Proxmox-API aus (keine Schreibfunktionen).

### Installation und Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python generate_hash.py      # Hash ausgeben und als ADMIN_PASSWORD_HASH in .env eintragen
# SECRET_KEY, PVE_HOST, PVE_TOKEN_ID und PVE_TOKEN_SECRET in .env setzen
python run.py                # http://127.0.0.1:5000
```

Der API-Token aus dem Abschnitt oben (`PVEAuditor`) wird als `PVE_TOKEN_ID` (`monitor-user@pve!monitoring-token`) und `PVE_TOKEN_SECRET` verwendet.

### Sicherheit

- Das Passwort wird nur als Hash (`ADMIN_PASSWORD_HASH`) konfiguriert und beim Login per Hash-Check geprüft.
- Cookie-basierte Session (HttpOnly, SameSite=Lax), CSRF-Schutz für Login/Logout; alle Seiten außer `/login` erfordern eine Anmeldung.
- Für den Produktivbetrieb hinter HTTPS `SESSION_COOKIE_SECURE=true` setzen und einen WSGI-Server (z. B. gunicorn `run:app`) nutzen. `PVE_VERIFY_SSL=true` beibehalten.

### Gast-Details (IP, Dienste, Host-Info, Kommentare)

Auf der Seite „VMs / Container pro Node“ werden für laufende Gäste IP-Adressen (ohne Loopback/link-local), offene Dienste und optional `/srv/info/host.info` angezeigt. Zusätzlich kann pro VM ein Kommentar gespeichert werden (max. 2000 Zeichen, Speicherung lokal in `COMMENTS_DB`, Standard `data/comments.json`).

Voraussetzungen:

- **QEMU**: QEMU Guest Agent muss auf der Gast-VM installiert und aktiviert sein, damit IPs, Host-Info und weitere Agent-Daten verfügbar sind. Ohne Agent wird die IP als „unbekannt“ angezeigt.
- **LXC**: IPs über `/nodes/{node}/lxc/{vmid}/interfaces`. `host.info` wird für LXC nicht unterstützt, da die Proxmox-API keinen lesenden Endpoint dafür bietet (ggf. später per SSH).
- **Token-Rechte**: `PVEAuditor` allein reicht evtl. nicht für Agent-Abfragen. Zusätzlich eine Rolle mit `VM.GuestAgent.Audit` und `VM.GuestAgent.FileRead` (PVE 9) bzw. `VM.Monitor` (PVE 8) vergeben, z. B.:

```bash
pveum role add GuestAgentRead -privs "VM.GuestAgent.Audit VM.GuestAgent.FileRead"   # PVE 9
pveum role add GuestAgentRead -privs "VM.Monitor"                                   # PVE 8
pveum acl modify / -user monitor-user@pve -role GuestAgentRead
```

QEMU Guest Agent auf der Gast-VM installieren und aktivieren:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent

# Fedora / RHEL
sudo dnf install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent

# openSUSE
sudo zypper install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent
```

Anschließend in Proxmox bei der VM unter **Options** → **QEMU Guest Agent** aktivieren. Den Agent-Status in der Gast-VM mit `sudo systemctl status qemu-guest-agent` oder die Verbindung auf dem Proxmox-Host mit `qm agent <VMID> ping` prüfen.

- **Dienste**: TCP-Connect-Check von der App zur IP des Gasts (Ports per `SERVICE_PORTS`, abschaltbar mit `SERVICE_CHECK=false`).

### Kanbanboard

Unter `/kanban` zeigt das passwortgeschützte Board vorhandene VMs und LXC-Container getrennt nach laufend/gestoppt. ToDos können angelegt, bearbeitet, gelöscht und per Drag & Drop zwischen „Geplant / ToDo“, „In Arbeit“ und „Erledigt“ verschoben werden; optional lassen sie sich einer VM bzw. einem Container zuordnen. Änderungen an ToDos erfordern die bestehende Anmeldung und den CSRF-Schutz. Ist Proxmox nicht erreichbar, bleiben ToDos und Host-Info weiterhin verfügbar.

ToDos werden thread-sicher in einer JSON-Datei gespeichert. `KANBAN_TODOS_DB` legt den Speicherort fest (Standard: `data/kanban-todos.json`, unter `DATA_DIR`, falls gesetzt). `HOST_INFO_FILE` konfiguriert die optionale Host-Info-Datei (Standard: `/srv/info/host.info`); maximal 64 KiB werden gelesen und im Board als escaped, vorformatierter Text angezeigt. Eine fehlende oder nicht lesbare Datei wird ignoriert.

Tests: `python -m pytest`
