#!/bin/sh
# Installiert prox-agent (als root ausfuehren).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
install -m 755 -o root -g root "$SRC/prox-agent" /usr/bin/prox-agent
install -d -m 755 -o root -g root /usr/lib/prox-agent/plugin /usr/lib/prox-agent/local /etc/prox-agent
for f in "$SRC"/plugin/*.example; do install -m 755 -o root -g root "$f" /usr/lib/prox-agent/plugin/; done
for f in "$SRC"/local/*.example; do
    [ -e "/usr/lib/prox-agent/local/$(basename "$f")" ] || install -m 755 -o root -g root "$f" /usr/lib/prox-agent/local/
done
if [ ! -e /etc/prox-agent/prox-agent.conf ]; then
    install -m 600 -o root -g root "$SRC/config/prox-agent.conf" /etc/prox-agent/prox-agent.conf
fi
install -d -m 755 /srv/info
install -m 644 -o root -g root "$SRC/systemd/prox-agent.service" /usr/lib/systemd/system/prox-agent.service
systemctl daemon-reload
systemctl enable --now prox-agent.service
echo "prox-agent installiert."
