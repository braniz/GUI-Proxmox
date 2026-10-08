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
- **Gast-Detailseite**: Klick auf den Hostnamen öffnet `/guests/<vmid>` (Hostname, IPs, Filesystem, CPU-Last per QEMU Guest Agent; bei LXC nur Basisdaten). Zusätzlich: Betriebssystem, angemeldete Benutzer, Gastzeit, Dateisystem-Freeze (nur Status), letztes apt-Update und die VM-Konfiguration (`qm config`, sensible Werte maskiert). Der Abschnitt „Letztes apt-Update“ liest per Guest Agent (nur lesend, kein `guest exec`) die Datei `/srv/info/host_info` und erwartet darin eine Zeile mit dem Präfix `apt-update:` (z. B. `apt-update: 2024-05-01 10:11:12 +0200`); fehlt die Datei oder die Zeile, zeigt der Abschnitt „Nicht verfügbar“. Der Agent muss in Proxmox unter „Options > QEMU Guest Agent“ aktiviert sein.
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

- **Dienste**: TCP-Connect-Check von der App zur IP des Gasts (Standard-Ports für ssh, http, https u. a.; die extern überwachten Dienste werden im Tab „Dienstüberwachung“ gewählt).

### Kanbanboard

Unter `/kanban` zeigt das passwortgeschützte Board vorhandene VMs und LXC-Container getrennt nach laufend/gestoppt. ToDos können angelegt, bearbeitet, gelöscht und per Drag & Drop zwischen „Geplant / ToDo“, „In Arbeit“ und „Erledigt“ verschoben werden; optional lassen sie sich einer VM bzw. einem Container zuordnen. Änderungen an ToDos erfordern die bestehende Anmeldung und den CSRF-Schutz. Ist Proxmox nicht erreichbar, bleiben ToDos und Host-Info weiterhin verfügbar.

**Dienstüberwachung & automatische ToDos**: Im Tab „Dienstüberwachung“ (`/services`) wird pro Server/Gast gewählt, welche Dienste (ssh, http, https, ftp, smtp, dns, smb, mysql, rdp, postgres) von außen per TCP-Connect überwacht werden. Die Auswahl wird in `data/service-monitoring.json` gespeichert (`SERVICE_MONITOR_DB` bzw. `DATA_DIR`); Änderungen erfordern Anmeldung und CSRF-Schutz. Das Öffnen von `/kanban` legt für jeden laufenden Gast mit ermittelbarer IP, bei dem ein gewählter Dienst nicht erreichbar ist, ein ToDo in „Geplant / ToDo“ an. Pro Gast und Dienst wird kein zweites ToDo angelegt, solange das vorhandene nicht „Erledigt“ ist. Die früheren `.env`-Variablen `SERVICE_CHECK`, `SERVICE_PORTS`, `AUTO_TODO_ENABLED`, `REQUIRED_SERVICES` und `SERVICE_MONITOR_INTERVAL` entfallen.

**Mehrfachbearbeitung & Historie**: Jede ToDo-Karte hat eine Checkbox; pro Spalte wählt „Alle auswählen“ alle ToDos der Spalte. Sobald mindestens ein ToDo ausgewählt ist, erscheint eine Aktionsleiste mit der Anzahl und den Aktionen Verschieben, Bearbeiten (Titel/Beschreibung/VMID für alle Ausgewählten), Erledigen und Löschen (`POST /api/kanban/todos/bulk` mit `ids`, `action`, `comment` und aktionsspezifischen Feldern; Antwort mit Ergebnis pro ToDo). Jede Aktion – auch an einzelnen ToDos (`PUT`/`DELETE /api/kanban/todos/<id>`) – erfordert einen nicht leeren Kommentar (client- und serverseitig, sonst HTTP 400). Pro Aktion wird am ToDo ein unveränderlicher Historieneintrag (Zeit in UTC, angemeldeter Benutzer, Aktion, Kommentar) vom Server erzeugt; Zeit und Benutzer können vom Client nicht vorgegeben werden, Einträge sind weder über UI noch API änderbar oder löschbar und werden auf der Karte in einem aufklappbaren Bereich („Historie“) in de-DE-Zeitformat angezeigt. Gelöschte ToDos bleiben mit ihrer Historie in einem separaten Audit-Log neben der ToDo-Datei erhalten (`kanban-todos-audit.json`).

**apt-Update-Überwachung**: Beim Öffnen von `/kanban` wird für jeden laufenden QEMU-Gast die Zeile `apt-update:` aus `/srv/info/host_info` (Guest Agent) gelesen. Liegt der Zeitstempel länger als `APT_UPDATE_MAX_AGE_DAYS` Tage zurück (Standard: 30; ohne Zeitzone gilt UTC), wird ein ToDo „apt-Update überfällig: …“ in „Geplant / ToDo“ angelegt (Historie: Benutzer `apt-monitor`); pro Gast höchstens ein offenes ToDo.

ToDos werden thread-sicher in einer JSON-Datei gespeichert. `KANBAN_TODOS_DB` legt den Speicherort fest (Standard: `data/kanban-todos.json`, unter `DATA_DIR`, falls gesetzt). `HOST_INFO_FILE` konfiguriert die optionale Host-Info-Datei (Standard: `/srv/info/host.info`); maximal 64 KiB werden gelesen und im Board als escaped, vorformatierter Text angezeigt. Eine fehlende oder nicht lesbare Datei wird ignoriert.

Tests: `python -m pytest`

## Erweiterung / Optional

### helper/

Im Verzeichnis `helper/` liegen kleine Hilfsskripte und Beispiel-Konfigurationen für die Pflege von Gast-Informationen und die apt-Update-Überwachung:

- `helper/check-apt-update.sh` aktualisiert ausschließlich die Zeile `apt-update:` in `/srv/info/host_info`; andere Einträge bleiben erhalten.
- `helper/check-apt-update.cron` zeigt einen Beispiel-Cron-Eintrag für die regelmäßige Ausführung des Scripts.

Die Dateien ergänzen die Gast-Detailseite und die apt-Update-Überwachung der GUI.

### host.info automatisch erzeugen

`/srv/info/host.info` ist ein **statischer Text**, der von der GUI nur gelesen und angezeigt, aber **nicht ausgeführt** wird. Der Inhalt kann daher in der Gast-VM automatisch per Bash-Script erzeugt werden, z. B. mit Hostname und letztem Reboot.

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

Im Verzeichnis [`agent/`](agent/README.md) liegt ein systemd-Agent, der auf VMs installiert wird und Systeminformationen als JSON bereitstellt (Erweiterungen unter `/usr/lib/prox-agent/plugin` und `/usr/lib/prox-agent/local`).
