#!/bin/sh
# Entfernt prox-agent. Konfiguration und local/ bleiben erhalten (PURGE=1 loescht sie).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren" >&2; exit 1; }
systemctl disable --now prox-agent.service 2>/dev/null || true
rm -f /usr/lib/systemd/system/prox-agent.service /usr/bin/prox-agent
rm -rf /usr/lib/prox-agent/plugin
systemctl daemon-reload
if [ "${PURGE:-0}" = "1" ]; then
    rm -rf /usr/lib/prox-agent /etc/prox-agent /var/lib/prox-agent
fi
echo "prox-agent entfernt."
