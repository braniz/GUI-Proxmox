#!/bin/sh
# Baut ein einfaches .deb in agent/build/. Erfordert dpkg-deb.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/prox-agent")"
ROOT="$SRC/build/prox-agent_${VERSION}"
rm -rf "$ROOT"
install -d -m 755 "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/prox-agent/plugin" \
    "$ROOT/usr/lib/prox-agent/local" "$ROOT/usr/lib/systemd/system" "$ROOT/etc/prox-agent"
install -m 755 "$SRC/prox-agent" "$ROOT/usr/bin/prox-agent"
install -m 755 "$SRC"/plugin/*.example "$ROOT/usr/lib/prox-agent/plugin/"
install -m 644 "$SRC/systemd/prox-agent.service" "$ROOT/usr/lib/systemd/system/"
install -m 600 "$SRC/config/prox-agent.conf" "$ROOT/etc/prox-agent/prox-agent.conf"
echo /etc/prox-agent/prox-agent.conf > "$ROOT/DEBIAN/conffiles"
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: prox-agent
Version: $VERSION
Architecture: all
Maintainer: GUI-Proxmox
Depends: python3
Description: Systeminformations-Agent fuer GUI-Proxmox
CTRL
cat > "$ROOT/DEBIAN/postinst" <<'S'
#!/bin/sh
set -e
mkdir -p /usr/lib/prox-agent/local /srv/info
systemctl daemon-reload || true
systemctl enable --now prox-agent.service || true
S
cat > "$ROOT/DEBIAN/prerm" <<'S'
#!/bin/sh
set -e
[ "$1" = "remove" ] && systemctl disable --now prox-agent.service || true
exit 0
S
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/prerm"
dpkg-deb --build "$ROOT"
