from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import backtest, paper
from .config import Config


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m trading_bot")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. --set symbols=XLF,EEM --set max_positions=5")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="simulate the strategy on historical data")
    bt.add_argument("--start", default="2016-01-01")
    bt.add_argument("--end")
    bt.add_argument("--refresh", action="store_true", help="re-download price data")
    bt.add_argument("--out", default="results")
    bt.add_argument("--structure", choices=["long_option", "cash_secured_put", "shares", "core_dip"],
                    help="override options.structure from the config")
    bt.add_argument("-v", "--verbose", action="store_true", help="print every signal and fill")

    tr = sub.add_parser("trend", help="backtest a monthly trend-following portfolio")
    tr.add_argument("--rule", choices=["sma", "momentum", "equal"], default="sma")
    tr.add_argument("--symbols", default="SPY,EFA,EEM,TLT,IEF,GLD,DBC,VNQ")
    tr.add_argument("--sma", type=int, default=200)
    tr.add_argument("--lookback", type=int, default=252, help="momentum lookback in trading days")
    tr.add_argument("--top", type=int, default=3)
    tr.add_argument("--cost-bps", type=float, default=5.0)
    tr.add_argument("--start", default="2007-03-01")
    tr.add_argument("--end")
    tr.add_argument("--refresh", action="store_true")
    tr.add_argument("--out", default="results")

    pp = sub.add_parser("paper", help="advance the paper-trading ledger to the latest close")
    pp.add_argument("--ledger", default="paper/ledger.json")
    pp.add_argument("--quotes", help="JSON {position label: real option mid} to mark with live prices")

    args = ap.parse_args()
    cfg = Config.load(args.config)
    for item in args.set:
        cfg.override(*item.split("=", 1))

    if args.cmd == "backtest":
        if args.structure:
            cfg.structure = args.structure
        # load extra history so the 200-day SMA is warm at --start
        warm = str(int(args.start[:4]) - 1) + args.start[4:]
        frames = backtest.build_frames(cfg, warm, args.refresh)
        engine, eq, trades = backtest.run(cfg, frames, args.start, args.end)
        if args.verbose:
            print("\n".join(engine.log))
        m = backtest.metrics(eq, trades, cfg.starting_cash)
        print(f"Strategy ({cfg.structure}, {', '.join(cfg.symbols)}):")
        print(backtest.fmt(m))
        print(f"Benchmark (buy & hold {cfg.symbols[0]}):")
        print(backtest.fmt(backtest.benchmark(frames, eq, cfg.symbols[0])))
        backtest.save(eq, trades, Path(args.out))
        print(f"\nWrote {args.out}/equity.csv and {args.out}/trades.csv")
    elif args.cmd == "trend":
        from . import trend

        p = trend.TrendParams(args.rule, args.sma, args.lookback, args.top, args.cost_bps)
        syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
        equity, targets, m = trend.run(syms, p, args.start, args.end, args.refresh)
        print(f"Trend ({args.rule}, {', '.join(syms)}):")
        print(backtest.fmt(m))
        bench = trend.run(["SPY"], trend.TrendParams("equal", cost_bps=args.cost_bps),
                          m["start"], args.end)[2]
        print("Benchmark (buy & hold SPY):")
        print(backtest.fmt({k: bench[k] for k in ("cagr", "max_drawdown", "sharpe", "sortino", "worst_year")}))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        equity.to_csv(out / "trend_equity.csv")
        targets.to_csv(out / "trend_weights.csv")
        print(f"\nLatest target weights ({targets.index[-1].date()}):")
        for sym, w in targets.iloc[-1].items():
            print(f"  {sym:<5} {w:.0%}")
        print(f"  cash  {1 - targets.iloc[-1].sum():.0%}")
    else:
        engine = paper.run_paper(cfg, Path(args.ledger), Path(args.quotes) if args.quotes else None)
        print("\n".join(engine.log[-30:]))
        print(paper.summary(engine, cfg))


if __name__ == "__main__":
    main()
