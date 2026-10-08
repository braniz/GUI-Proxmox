#!/bin/bash
set -euo pipefail

INFO_FILE="/srv/info/host_info"
KEY="apt-update"

mkdir -p /srv/info
touch "$INFO_FILE"

TS="$(stat -c %y /var/lib/apt/lists/)"

if grep -qi "^${KEY}:" "$INFO_FILE"; then
    sed -i "s|^${KEY}:.*|${KEY}: ${TS}|I" "$INFO_FILE"
else
    echo "${KEY}: ${TS}" >> "$INFO_FILE"
fi
