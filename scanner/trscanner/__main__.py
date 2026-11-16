"""Kommandozeile: python -m trscanner <befehl>

  serve      API + Dashboard + Hintergrundjobs starten (Echtzeitbetrieb)
  train      Historie laden, Walk-Forward-Backtest je Horizont, Modelle speichern
  scan       einen einzelnen Scan ausführen und die Top-Treffer ausgeben
  report     letzten Backtest-Bericht ausgeben
  news       Nachrichten einmal einsammeln
  outcomes   Live-Signale nachbewerten
  resolve    fehlende Ticker zu ISINs per OpenFIGI ergänzen
  demo       Demo mit simulierten Daten: trainieren und Server starten (ohne Internet)
  export     ein Scan-Durchlauf und statische Dashboard-Daten schreiben (für GitHub Pages, ohne Server)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trscanner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["serve", "train", "scan", "report", "news", "outcomes", "resolve", "demo", "export"])
    parser.add_argument("--out", default="site", help="Zielordner für export")
    parser.add_argument("--no-train", action="store_true", help="export: nicht automatisch trainieren")
    parser.add_argument("--horizons", help="z. B. 1h,4h,1d (Standard: alle)")
    parser.add_argument("--no-backfill", action="store_true", help="beim Training keine SEC-Historie laden")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "demo":
        os.environ["TRS_DATA_PROVIDER"] = "synthetic"
        os.environ.setdefault("TRS_DATA_DIR", os.path.join(os.getcwd(), "data-demo"))
    from .config import reload_settings, settings

    if args.command == "demo":
        reload_settings()

    if args.command == "resolve":
        from .universe import resolve_isins

        print(f"{resolve_isins()} ISINs aufgelöst")
        return 0

    from .engine import Engine

    if args.command in ("serve", "demo"):
        import uvicorn

        from .api import create_app

        eng = Engine()
        if args.command == "demo" and not eng.models:
            print("Demo: trainiere Modelle auf simulierten Daten …")
            eng.train()
        uvicorn.run(create_app(eng), host=args.host or settings.api_host, port=args.port or settings.api_port)
        return 0

    eng = Engine()
    horizons = args.horizons.split(",") if args.horizons else None
    if args.command == "train":
        reports = eng.train(horizons, backfill_news=not args.no_backfill)
        _print_report(reports)
    elif args.command == "report":
        from .db import kv_get

        _print_report(kv_get("backtest_report", {}) or {})
    elif args.command == "news":
        print(f"{eng.refresh_news()} neue Meldungen")
    elif args.command == "export":
        from pathlib import Path

        from .export import run_once_and_export

        print(f"{run_once_and_export(eng, Path(args.out), train_if_missing=not args.no_train)} Dateien exportiert")
    elif args.command == "outcomes":
        print(f"{eng.evaluate_outcomes()} Signale nachbewertet")
    elif args.command == "scan":
        eng.refresh_news()
        eng.update_market_data(full=True)
        snaps = sorted(eng.scan(), key=lambda s: -s["score"])[: args.top]
        print(f"{'Aktie':<10} {'Score':>5} {'Horiz.':>6} {'Konfl.':>6} {'Risiko':<10} Ereignis")
        for s in snaps:
            print(f"{s['symbol']:<10} {s['score']:>5} {s['best_horizon'] or '-':>6} {s['confluence']:>6} {s['risk']['level']:<10} {s['event']}")
        if snaps and snaps[0]["alertable"]:
            from .alerts import format_alert

            print("\n" + format_alert(snaps[0]))
    return 0


def _print_report(reports: dict) -> None:
    if not reports:
        print("Noch kein Backtest vorhanden. Zuerst: python -m trscanner train")
        return
    print(f"\n{'Horizont':<9} {'Status':<14} {'AUC':>5} {'Basis':>6} {'Treffer':>8} {'Lift':>5} {'Trades':>7} {'Ø netto':>8} {'95%-KI':>17} {'MaxDD':>7}")
    for key, r in reports.items():
        if r.get("status") == "fehlgeschlagen":
            print(f"{key:<9} fehlgeschlagen: {r.get('error')}")
            continue
        c, t = r["classification"], r["trades"]
        ci = t.get("mean_net_ret_ci95") or [float("nan")] * 2
        print(f"{key:<9} {r['status']:<14} {c.get('roc_auc', float('nan')):>5.2f} {c['base_rate'] * 100:>5.1f}% "
              f"{(t.get('hit_rate') or 0) * 100:>7.1f}% {r['lift_trades']:>5.2f} {t.get('n', 0):>7} "
              f"{(t.get('mean_net_ret') or 0) * 100:>7.2f}% [{(ci[0] or 0) * 100:>6.2f}; {(ci[1] or 0) * 100:>6.2f}]% "
              f"{(t.get('max_drawdown') or 0) * 100:>6.1f}%")
    if any(r.get("demo") for r in reports.values()):
        print("\nACHTUNG: Demo-Modus – simulierte Daten, Ergebnisse sagen nichts über echte Märkte aus.")


if __name__ == "__main__":
    sys.exit(main())
