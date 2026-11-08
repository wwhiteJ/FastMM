#!/usr/bin/env bash
set -euo pipefail
test ! -e /var/lib/fastmm/paper/PAUSED
release=/opt/fastmm-retention/current
run_dir=/var/lib/fastmm/retention/observe-$(date -u +%Y%m%dT%H%M%S%N)
mkdir -p "$run_dir"
python3 - "$release" "$run_dir" <<'PY'
import sys
import time
from pathlib import Path
release, run = map(Path, sys.argv[1:])
sys.path.insert(0, str(release / 'paper'))
from retention.artifacts import override_toml
source = (release / 'paper/btcusdt.toml').read_text()
config = override_toml(source, {
    'engine': {'name': 'btc-paper-dryrun'},
    'strategy.params': {'gate_mode': 1, 'trace_quotes': False, 'gate_trace_start_ns': time.time_ns()}})
(run / 'observer.toml').write_text(config)
PY
ln -sfn "$run_dir" /var/lib/fastmm/retention/latest
exec "$release/bin/fastmm-paper-observe" "$run_dir/observer.toml" "$run_dir"
