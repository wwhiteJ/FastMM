#!/bin/sh
set -eu
rows_file="/var/lib/fastmm/paper/rows-$(date -u +%Y%m%dT%H%M%S%N).jsonl"
exec /opt/fastmm/bin/fastmm-shadow --config /opt/fastmm/etc/shadow.toml --rows "$rows_file"
