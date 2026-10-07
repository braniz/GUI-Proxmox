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
