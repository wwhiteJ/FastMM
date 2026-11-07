#!/bin/bash
set -euo pipefail
package_dir=$(cd "${1:?usage: install.sh PACKAGE_DIR}" && pwd)
if ! id fastmm >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/fastmm --shell /usr/sbin/nologin fastmm
fi
install -d /opt/fastmm/bin /opt/fastmm/etc /opt/fastmm/paper
install -d -o fastmm -g fastmm -m 0750 /var/lib/fastmm/paper /var/lib/fastmm/paper/runs
install -m 0755 "$package_dir"/bin/fastmm-* /opt/fastmm/bin/
install -m 0644 "$package_dir"/paper/*.py /opt/fastmm/paper/
install -m 0755 "$package_dir"/paper/*.sh /opt/fastmm/paper/
install -m 0644 "$package_dir"/paper/btcusdt.toml /opt/fastmm/etc/btcusdt.toml
sed 's/name = "btc-paper"/name = "btc-paper-dryrun"/' \
    /opt/fastmm/etc/btcusdt.toml > /opt/fastmm/etc/shadow.toml
install -m 0644 "$package_dir"/paper/*.service "$package_dir"/paper/*.timer /etc/systemd/system/
systemd-analyze verify "$package_dir"/paper/*.service "$package_dir"/paper/*.timer
systemctl daemon-reload
systemctl enable --now fastmm-paper-feed.service fastmm-paper-shadow.service \
    fastmm-paper-report.timer fastmm-paper-disk.timer
