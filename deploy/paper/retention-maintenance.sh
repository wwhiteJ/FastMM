#!/usr/bin/env bash
set -euo pipefail
available_kib=$(df -Pk /var/lib/fastmm/retention | awk 'NR==2 {print $4}')
if [ "$available_kib" -lt 5242880 ] || [ -e /var/lib/fastmm/paper/PAUSED ]; then
    systemctl stop fastmm-retention-observe.service
fi
/usr/sbin/logrotate --state /var/lib/fastmm/retention/logrotate.state /opt/fastmm-retention/current/paper/retention-logrotate.conf
