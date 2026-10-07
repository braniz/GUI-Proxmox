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

Tests: `python -m pytest`
