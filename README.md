# GUI-Proxmox

Proxmox Projekt für die Webbasierte Darstellung und Management von Proxmox Clustern.<br>
Das ganze Projekt wurde mit GitHub copilot erstellt.

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

Passwortgeschützte Flask-Weboberfläche zur Anzeige von Cluster-Übersicht, Nodes, VMs/Containern und Storage. Die App führt ausschließlich `GET`-Anfragen gegen die Proxmox-API aus (keine Schreibzugriffe).

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

Auf der Seite „VMs / Container pro Node“ werden für laufende Gäste IP-Adressen (ohne Loopback/link-local), offene Dienste und optional `/srv/info/host.info` angezeigt. Zusätzlich kann pro VM eine Kommentar-Notiz hinterlegt werden.

Voraussetzungen:

- **QEMU**: QEMU Guest Agent muss auf der Gast-VM installiert und aktiviert sein, damit IPs, Host-Info und weitere Agent-Daten verfügbar sind. Ohne Agent wird die IP als „unbekannt“ angezeigt.
- **LXC**: IPs über `/nodes/{node}/lxc/{vmid}/interfaces`. `host.info` wird für LXC nicht unterstützt, da die Proxmox-API keinen lesenden Endpoint dafür bietet (ggf. später per SSH).
- **Gast-Detailseite**: Klick auf den Hostnamen öffnet `/guests/<vmid>` (Hostname, IPs, Filesystem, CPU-Last per QEMU Guest Agent; bei LXC nur Basisdaten). Zusätzlich: Betriebssystem, angemeldete Benutzer, Uptime, `fsfreeze`-Status und weitere Agent-Infos.
- **Token-Rechte**: `PVEAuditor` allein reicht evtl. nicht für Agent-Abfragen. Zusätzlich eine Rolle mit `VM.GuestAgent.Audit` und `VM.GuestAgent.FileRead` (PVE 9) bzw. `VM.Monitor` (PVE 8) vergeben:

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

Anschließend in Proxmox bei der VM unter **Options** → **QEMU Guest Agent** aktivieren. Den Agent-Status in der Gast-VM mit `sudo systemctl status qemu-guest-agent` oder die Verbindung auf dem Host prüfen.

- **Dienste**: TCP-Connect-Check von der App zur IP des Gasts (Standard-Ports für ssh, http, https u. a.; die extern überwachten Dienste werden im Tab „Dienstüberwachung“ gewählt).

### Kanbanboard

Unter `/kanban` zeigt das passwortgeschützte Board vorhandene VMs und LXC-Container getrennt nach laufend/gestoppt. ToDos können angelegt, bearbeitet, gelöscht und per Drag & Drop zwischen „Geplant“, „In Arbeit“ und „Fertig“ verschoben werden.

**Dienstüberwachung & automatische ToDos**: Im Tab „Dienstüberwachung“ (`/services`) wird pro Server/Gast gewählt, welche Dienste (ssh, http, https, ftp, smtp, dns, smb, mysql, rdp, postgresql, mongodb, redis) überwacht werden. Fällt ein Dienst aus oder ist ein Gast nicht erreichbar, legt die App automatisch ein ToDo an oder aktualisiert die Historie; bestehende ToDos werden nicht dupliziert.

**Mehrfachbearbeitung & Historie**: Jede ToDo-Karte hat eine Checkbox; pro Spalte wählt „Alle auswählen“ alle ToDos der Spalte. Sobald mindestens ein ToDo ausgewählt ist, erscheint eine Aktionsleiste zum Verschieben, Abschließen oder Löschen mehrerer Karten auf einmal. Jede Änderung erhält eine kommentierte Historie.

**apt-Update-Überwachung**: Beim Öffnen von `/kanban` wird für jeden laufenden QEMU-Gast die Zeile `apt-update:` aus `/srv/info/host_info` (Guest Agent) gelesen. Liegt der Zeitstempel länger als der konfigurierte Grenzwert zurück, wird automatisch ein ToDo erzeugt.

ToDos werden thread-sicher in einer JSON-Datei gespeichert. `KANBAN_TODOS_DB` legt den Speicherort fest (Standard: `data/kanban-todos.json`, unter `DATA_DIR`, falls gesetzt). `HOST_INFO_FILE` konfiguriert die Datei für die Host-Info-Lesezugriffe.

Tests: `python -m pytest`

## Erweiterung / Optional

### helper/

Im Verzeichnis `helper/` liegen kleine Hilfsskripte und Beispiel-Konfigurationen für die Pflege von `/srv/info/host_info`.

#### Inhalt

- `helper/check-apt-update.sh`  
  Aktualisiert ausschließlich die Zeile `apt-update:` in `/srv/info/host_info`.  
  Alle anderen Inhalte der Datei bleiben unverändert.

- `helper/check-apt-update.cron`  
  Beispiel für einen Cron-Eintrag, der das Script regelmäßig ausführt.

#### Zweck

Die Dateien in `helper/` unterstützen die Gast-Detailseite und die apt-Update-Überwachung in der GUI.  
So kann die App den Zeitpunkt des letzten apt-Updates aus `/srv/info/host_info` lesen, ohne dass die Datei komplett neu geschrieben wird.

### host.info automatisch erzeugen

`/srv/info/host.info` ist ein **statischer Text**, der von der GUI nur gelesen und angezeigt, aber **nicht ausgeführt** wird. Der Inhalt kann daher in der Gast-VM automatisch per Bash-Script erzeugt werden.

Script `/usr/local/bin/update-host-info.sh` (ausführbar machen mit `sudo chmod +x /usr/local/bin/update-host-info.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="/srv/info/host.info"
MANUAL="/srv/info/host.info.manual"   # optional: manuelle Ergänzungen

mkdir -p "$(dirname "$TARGET")"
TMP="$(mktemp "${TARGET}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

. /etc/os-release

{
  echo "Hostname:        $(hostname)"
  echo "Letzter Reboot:  $(uptime -s)"
  echo "Uptime:          $(uptime -p)"
  echo "OS:              ${PRETTY_NAME:-unbekannt}"
  echo "Kernel:          $(uname -r)"
  echo "Aktualisiert:    $(date '+%Y-%m-%d %H:%M:%S')"
  if [ -r "$MANUAL" ]; then
    echo
    cat "$MANUAL"
  fi
} > "$TMP"

chmod 644 "$TMP"
mv -f "$TMP" "$TARGET"
trap - EXIT
```

Manuelle Ergänzungen (z. B. Zweck der VM, Ansprechpartner) können in `/srv/info/host.info.manual` stehen; der Inhalt wird vom Script an `host.info` angehängt.

### systemd Service + Timer

`/etc/systemd/system/host-info.service`:

```ini
[Unit]
Description=host.info aktualisieren

[Service]
Type=oneshot
ExecStart=/usr/local/bin/update-host-info.sh
```

`/etc/systemd/system/host-info.timer`:

```ini
[Unit]
Description=host.info beim Boot und regelmäßig aktualisieren

[Timer]
OnBootSec=30s
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

Aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now host-info.timer
sudo systemctl start host-info.service   # sofort einmal ausführen
```

### Hinweise

- Das Script schreibt **atomar**: erst in eine temporäre Datei, dann per `mv` an den Zielort. So liest die GUI nie eine halb geschriebene Datei.
- Die Datei muss lesbar sein (z. B. `644`), damit der QEMU Guest Agent sie lesen kann.
- Das Feature gilt nur für **QEMU-VMs** (nicht für LXC-Container).

## prox-agent (Gast-Agent)

Im Verzeichnis [`agent/`](agent/README.md) liegt ein systemd-Agent, der auf VMs installiert wird und Systeminformationen als JSON bereitstellt (Erweiterungen unter `/usr/lib/prox-agent/plugin` und `/usr/lib/prox-agent/local`). Der Agent kann optional die lokale Datei `/srv/info/host.info` mit ausliefern.
