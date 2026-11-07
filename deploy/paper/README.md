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

Build portable Release executables on the build host, with `FASTMM_NATIVE_ARCH=OFF`. The committed source archive accompanies the binaries. `BUILD.json` records the clean source commit and the binary/configuration digests.

Prepare a package with `bin/fastmm-live`, `bin/fastmm-shadow`, `bin/fastmm-top`, `bin/fastmm-backtest`, and this directory copied to `paper/`. Run `bash paper/install.sh PACKAGE_DIR` as root on the target. Installation starts the paper services; inspect existing services before using it for an upgrade.

The change adds an injected row observer to the existing oversized shadow implementation instead of refactoring its unrelated comparison logic. Export and accounting logic live in separate, testable components. Validate the observer with `fastmm_backtest_tests --test-case='*shadow*'`, and accounting with `python3 -m unittest discover -s deploy/paper -p 'test_*.py'`.

## BBO and inventory lifecycle

The paper configuration uses the original 3 bps half-spread. `basic_mm` prices from the newest locally delivered depth or bookTicker BBO, ordered by their common venue update ID (timestamps when IDs are unavailable). An older depth update cannot replace a newer ticker or refresh its local age. A newer invalid BBO pulls quotes; fresh valid data is needed to resume. Depth callbacks remain enabled even when the mid is unchanged, so freshness is maintained.

Ordinary quote changes retain the engine's 100 ms interval and price threshold. Quote withdrawals bypass that interval. A timer retries a deferred price change when no subsequent market move arrives. Inventory reduction latches at `inventory_reduce_pct` of the cap or `inventory_soft_ms` after the position first leaves zero; partial reductions do not restart this clock. It clears only at flat. Entering this state cancels the old ladder and waits for terminal order updates before quoting only the reducing side, with total quantity capped to the remaining position.

At `inventory_hard_ms`, the strategy cancels maker quotes, waits until no orders remain open, then sends reduce-only IOC limits. Each attempt is at most `quote_qty`, priced at most `exit_slippage_bps` beyond the current BBO, and attempts are separated by `exit_retry_ms`. Partial fills, rejected cancels, stale data, order rejections and missing liquidity can delay flatness; the timeout starts the exit process, rather than guaranteeing a fill at that instant. No IOC is sent while quoting is disabled or reconciliation is pending. The paper configuration's slippage limit is an execution assumption to validate in replay.

`risk.reduce_only_min_notional_exempt` is an explicit venue-policy opt-in for a flagged derivative order that only closes the unreserved position. It allows the hard exit to handle a residual below the entry minimum notional without reducing the entry guard. Wrong-side, oversized, unflagged and spot orders retain that guard; lot-size and other risk checks still apply. Enable this only where the venue supports the exception.

With `trace_quotes = true`, the shadow's `--verbose` log records decision time, chosen BBO and update ID, local market age, inventory age/state, interval deferrals and exit rejection reasons. These are asynchronous diagnostic logs: check `diagnostics.json` for dropped records in an offline replay. Simulated fills/equity remain in `rows-*.jsonl`.

## Replay and release verification

Build `fastmm-paper-replay` with the same portable preset as the deployed programs. It runs the exact Shadow path over a closed collector journal, preserving its recorded arrival times, exchange feed, fees, latency assumptions and engine throttles. The supplied configuration overrides strategy parameters and the reduce-only minimum-notional policy; parameter names are pinned against journal updates. It exports fills, equity, diagnostics and the shadow report:

```bash
build/release-nolto/bin/fastmm-paper-replay FIRST_PART.fmj PARAM_CONFIG.toml OUTPUT_DIR
```

Compare the preserved pre-fix executable with the new executable at the same 3 bps parameters, then enable inventory exits, then compare the 5 bps variant separately. Keep all parts of the closed journal and verify their hashes. For the baseline, verify fill rows and equity against the archived paper session before interpreting any comparison. Final PnL includes unrealized inventory and commissions; forward markouts are diagnostics, not additional realized losses.

Before an upgrade, commit the source on a task branch, run the lifecycle, risk, shadow and accounting checks, and build from that clean commit. Archive the running session's configuration, summary, rows, binaries and build manifest before stopping its collector. Install the release with a manifest recording the commit and SHA-256 of each binary/configuration; retain the previous package for rollback. A restart begins a new flat paper trial. Inspect service state, session ID, configuration, diagnostic logs and newly arriving equity bars after switching.
