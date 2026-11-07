# Japan paper deployment

The `aliyun-jp-3` deployment runs `basic_mm` on BTCUSDT perpetual public production data. `fastmm-live --dry-run` records the feed and acknowledges paper orders; `fastmm-shadow` supplies the simulated fills and inventory feedback. No account credentials are configured.

## Operate

Connect with `ssh aliyun-jp-3`. The installation is `/opt/fastmm`; writable data is `/var/lib/fastmm/paper`. Inspect the simulation's `simulated` column in the terminal monitor; the dry-run engine's own zero PnL and the shadow's comparison with that zero are not strategy results. The shadow can aggregate several collector sessions, so use `summary.json` for the current session and the pinned trial report for a particular evaluation window.

```bash
/opt/fastmm/bin/fastmm-top --name btc-paper-dryrun
cat /var/lib/fastmm/paper/summary.json
systemctl status fastmm-paper-feed fastmm-paper-shadow
journalctl -u fastmm-paper-feed -u fastmm-paper-shadow -n 60 --no-pager
```

Stop both processes with `systemctl stop fastmm-paper-feed fastmm-paper-shadow`. Start them with `systemctl start fastmm-paper-feed fastmm-paper-shadow`. The report and disk timers run once a minute. `summary.json` describes the current collector session; its `state` becomes `stale` when its latest simulated equity bar is over two minutes old.

## Configuration and accounting

`btcusdt.toml` owns the strategy, fees and latency assumptions. The installer generates `shadow.toml` with the dry-run session name. The shadow reads the strategy and backtest settings embedded in the collector's journal, so changing these settings requires starting a new collector session. A collector restart starts a new flat simulation; positions are not carried across these independent trials. Restarting only the shadow replays the current collector session from its beginning and reconstructs its position.

`rows-*.jsonl` contains baseline simulated fills and equity bars in raw 1e-8 fixed-point units, keyed by the collector session ID. Each shadow process writes a new file. The reporter reads the newest file, so a replay replaces the previous view without double-counting it. Equity is marked at the market mid, including unrealized PnL and simulated commissions. BNB-discount commissions are charged as their USDT equivalent; this does not model BNB inventory price changes.

The reporter estimates settled funding using Binance's public funding history and the simulated position immediately before each settlement timestamp. Positive rates debit longs and credit shorts. Funding is added only to the report, not to the engine's simulated balance or loss guard. Funding API failures make the including-funding net value unavailable instead of silently assuming zero. Recently settled funding can appear after a delay; the report labels funding as an estimate. Server costs are excluded.

Drawdown is sampled at the shadow's equity-bar interval and can miss intra-bar peaks and losses. Order and cancel latency are scenario assumptions until measured. Even conservative L2 queue settings cannot reconstruct exact queue priority or prove actual fillability. The summary is simulated PnL, not live performance.

## Fixed-window parameter trials

Before changing parameters, archive the stopped session's configuration, rows and final marked PnL under `/var/lib/fastmm/paper/trials/`. Keep its journals intact. A new collector session starts flat; the archived session's outstanding position and marked loss are not a realized close and must not be erased from the experiment history.

Each trial has an immutable `manifest.json` pinning its session ID, row file, configuration hash, hypothesis, equity interval and evaluation duration. `trial_report.py --manifest MANIFEST --out RESULT` evaluates only that session, including when the shadow's row file also contains older replayed sessions. The simulation starts one equity interval before its first regular equity bar, with zero position and zero PnL. A complete trial requires every expected equity bar through the exact endpoint. Early positive PnL does not qualify, and later rows cannot change the fixed-window result.

The stopping criterion is strictly positive endpoint PnL after trading fees, including unrealized PnL marked at mid and estimated settled funding. Infrastructure costs and hypothetical liquidation costs are excluded. Funding lookup failure prevents a success decision. A timer may publish the trial report while it runs; this reporting command never changes strategy parameters or stops trading. On success retain the strategy settings and stop further optimization; on failure inspect the completed result before deciding the next hypothesis. A positive trial is not evidence of persistent profitability.

## Data retention

The collector rotates journal parts at the configured size. No active journal parts are deleted: a shadow restart needs them to reconstruct the simulation. The disk guard creates `/var/lib/fastmm/paper/PAUSED` and stops both processes when free space falls below 5 GiB. Service start checks that file. Archive a complete stopped trial before removing its data; then remove `PAUSED` and start a new trial. Do not delete part of an active journal.

## Build and install

Build portable Release executables on the build host, with `FASTMM_NATIVE_ARCH=OFF`. The deployed source tree and patch accompany the binaries. `BUILD.json` records the source revision, patch digest and binary digests.

Prepare a package with `bin/fastmm-live`, `bin/fastmm-shadow`, `bin/fastmm-top`, `bin/fastmm-backtest`, and this directory copied to `paper/`. Run `bash paper/install.sh PACKAGE_DIR` as root on the target. Installation starts the paper services; inspect existing services before using it for an upgrade.

The change adds an injected row observer to the existing oversized shadow implementation instead of refactoring its unrelated comparison logic. Export and accounting logic live in separate, testable components. Validate the observer with `fastmm_backtest_tests --test-case='*shadow*'`, and accounting with `python3 -m unittest discover -s deploy/paper -p 'test_*.py'`.
