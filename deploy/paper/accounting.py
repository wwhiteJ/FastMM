"""Accounting for one BTCUSDT shadow session; all exported money uses raw 1e-8 units."""
from decimal import Decimal

SCALE = Decimal(100_000_000)
NS_PER_MS = 1_000_000


def funding_payments(fills, history, end_ns):
    """Settlement uses the simulated position immediately before the funding timestamp."""
    ordered = sorted(fills, key=lambda row: row["ts_ns"])
    position, index, payments = 0, 0, []
    for event in sorted(history, key=lambda row: row["fundingTime"]):
        at = event["fundingTime"] * NS_PER_MS
        if at > end_ns:
            break
        while index < len(ordered) and ordered[index]["ts_ns"] < at:
            fill = ordered[index]
            position += fill["qty_raw"] * (1 if fill["side"] == 0 else -1)
            index += 1
        mark = Decimal(event["markPrice"])
        rate = Decimal(event["fundingRate"])
        amount = -Decimal(position) / SCALE * mark * rate
        payments.append({"ts_ns": at, "position_btc": str(Decimal(position) / SCALE),
                         "mark_price": str(mark), "rate": str(rate), "amount_usdt": str(amount)})
    return payments


def summarize(rows, history, initial_capital):
    bars = [row for row in rows if row["type"] == "equity"]
    if not bars:
        return {"state": "waiting_for_first_equity_bar"}
    latest = bars[-1]
    session_id = latest["session_id"]
    bars = [row for row in bars if row["session_id"] == session_id]
    fills = [row for row in rows if row["type"] == "fill"
             and row["session_id"] == session_id and row["ts_ns"] <= latest["ts_ns"]]
    payments = funding_payments(fills, history, latest["ts_ns"])
    funding = sum((Decimal(p["amount_usdt"]) for p in payments), Decimal(0))
    fees = Decimal(latest["fees_raw"]) / SCALE
    gross = Decimal(latest["realized_raw"] + latest["unrealized_raw"]) / SCALE
    net = gross - fees + funding
    peak, drawdown = Decimal(0), Decimal(0)
    for bar in bars:
        paid = sum((Decimal(p["amount_usdt"]) for p in payments if p["ts_ns"] <= bar["ts_ns"]), Decimal(0))
        equity = Decimal(bar["realized_raw"] + bar["unrealized_raw"] - bar["fees_raw"]) / SCALE + paid
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    notional = sum((Decimal(f["price_raw"]) * Decimal(f["qty_raw"]) / SCALE**2 for f in fills), Decimal(0))
    return {"state": "running", "session_id": session_id, "as_of_ns": latest["ts_ns"],
            "fills": len(fills), "maker_fills": sum(f["liquidity"] == 1 for f in fills),
            "taker_fills": sum(f["liquidity"] == 2 for f in fills),
            "notional_usdt": str(notional), "fees_usdt": str(fees),
            "realized_before_fees_usdt": str(Decimal(latest["realized_raw"]) / SCALE),
            "unrealized_mid_usdt": str(Decimal(latest["unrealized_raw"]) / SCALE),
            "net_before_funding_usdt": str(gross - fees), "funding_usdt": str(funding),
            "net_including_funding_usdt": str(net), "position_btc": str(Decimal(latest["position_raw"]) / SCALE),
            "mid_price": str(Decimal(latest["mid_raw"]) / SCALE),
            "sampled_max_drawdown_usdt": str(drawdown),
            "sampled_max_drawdown_pct": str(drawdown / Decimal(initial_capital) * 100),
            "funding_payments": payments}
