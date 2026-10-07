# prox-agent

Systemd-basierter Agent (Python 3, nur Standardbibliothek), der auf einer VM/einem Gast installiert wird und Systeminformationen als JSON bereitstellt. Er ist eigenständig und importiert die Flask-App nicht.

## Installation

```bash
sudo ./install.sh      # installiert nach /usr/bin/prox-agent, aktiviert den Dienst
sudo ./uninstall.sh    # entfernt den Agenten (Konfiguration und local/ bleiben; PURGE=1 löscht alles)
./build-deb.sh         # optional: baut ein .deb (benötigt dpkg-deb); local/ bleibt bei Update/Entfernen erhalten
```

Installierte Pfade: `/usr/bin/prox-agent`, `/usr/lib/prox-agent/plugin`, `/usr/lib/prox-agent/local` (755, root), `/usr/lib/systemd/system/prox-agent.service`, `/etc/prox-agent/prox-agent.conf` (wird nicht überschrieben).

## Benutzung

```bash
prox-agent --once            # JSON ausgeben und beenden
prox-agent --serve           # Daemon-Modus (Snapshot-Datei, optional HTTP)
prox-agent --version
prox-agent --config PFAD
```

Es wurde ein dauerhafter Dienst (`prox-agent.service`) statt eines Timers gewählt, da so optional HTTP bereitgestellt werden kann. Für reinen Oneshot-Betrieb kann `prox-agent --once` per eigenem Timer aufgerufen werden.

## Konfiguration (`/etc/prox-agent/prox-agent.conf`, INI, Abschnitt `[agent]`)

| Schlüssel | Standard | Beschreibung |
|---|---|---|
| `interval` | `60` | Intervall in Sekunden |
| `services` | `ssh, sshd, cron, …` | systemd-Dienste für `systemctl is-active` |
| `bind` / `port` | `127.0.0.1` / `0` | HTTP-Endpunkt; `port = 0` deaktiviert ihn (sicherster Standard) |
| `token` | leer | Shared Secret; Zugriff mit Header `Authorization: Bearer <token>` |
| `output_file` | `/var/lib/prox-agent/info.json` | JSON-Snapshot |
| `host_info_file` | leer | optionale lesbare Datei, z. B. `/srv/info/host.info` |
| `plugin_timeout` | `10` | Timeout pro Plugin (s) |

## JSON-Schema (Auszug)

`agent_version`, `timestamp`, `hostname`, `fqdn`, `os{name,pretty_name,id,version_id,kernel,architecture}`, `uptime_seconds`, `boot_time`, `cpu{model,cores,load_average}`, `memory{total_bytes,available_bytes,used_bytes,swap_total_bytes,swap_used_bytes}`, `disks[{device,mount,fstype,total_bytes,used_bytes,free_bytes}]`, `network{<if>:{mac,state,addresses}}`, `updates{manager,count}`, `reboot_required`, `services{<name>:<status>}`, `logged_in_users`, `plugins{}`, `local{}`.

## Plugins

Ausführbare Dateien in `/usr/lib/prox-agent/plugin` (mitgelieferte Erweiterungen) und `/usr/lib/prox-agent/local` (eigene, werden bei Updates nicht überschrieben). Vertrag:

- Die Datei wird mit Timeout (`plugin_timeout`) ohne Argumente ausgeführt und gibt gültiges JSON auf stdout aus, Exit-Code 0.
- Ergebnis erscheint unter `plugins.<name>` bzw. `local.<name>` (Name = Dateiname ohne `.py`/`.sh`).
- Fehler (Timeout, ungültiges JSON, Exit-Code ≠ 0) werden geloggt und als `{"error": "..."}` eingetragen; der Agent läuft weiter.
- Dateien mit Endung `.example`, versteckte und nicht ausführbare Dateien werden ignoriert. Beispiele: `plugin/example.sh.example`, `local/local.sh.example` (zum Aktivieren Endung entfernen).

## Sicherheit

- Plugins werden nur ausgeführt, wenn sie root gehören und weder gruppen- noch welt-beschreibbar sind; sonst übersprungen (Fehlermarker).
- HTTP ist standardmäßig aus; bei Bindung an eine nicht-lokale Adresse ist ein Token Pflicht, sonst startet der Server nicht. Der Endpunkt ist read-only (nur GET).
- Die Konfigurationsdatei (mit Token) wird mit Modus 600 installiert. Die Unit nutzt `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, `PrivateTmp`.
- Tests: `pytest agent/tests`
