#!/usr/bin/env python3
"""BlackRoad Crypto Tracker - Production Module.

Cryptocurrency portfolio tracker with transaction history,
price tracking, and unrealized P&L calculation.
"""

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

RED     = "\033[0;31m"
GREEN   = "\033[0;32m"
YELLOW  = "\033[1;33m"
CYAN    = "\033[0;36m"
BLUE    = "\033[0;34m"
MAGENTA = "\033[0;35m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
NC      = "\033[0m"

DB_PATH = Path.home() / ".blackroad" / "crypto_tracker.db"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    symbol: str
    name: str
    total_qty: float = 0.0
    avg_cost: float = 0.0
    current_price: float = 0.0
    created_at: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class Transaction:
    symbol: str
    tx_type: str     # buy | sell
    quantity: float
    price_usd: float
    fee_usd: float = 0.0
    exchange: str = "manual"
    tx_date: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if not self.tx_date:
            self.tx_date = datetime.now().isoformat()


@dataclass
class PriceRecord:
    symbol: str
    price_usd: float
    source: str = "manual"
    recorded_at: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if not self.recorded_at:
            self.recorded_at = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Database / Business Logic
# ---------------------------------------------------------------------------

class CryptoTracker:
    """Production cryptocurrency portfolio tracker with P&L analysis."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS holdings (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT UNIQUE NOT NULL,
                    name          TEXT NOT NULL,
                    total_qty     REAL DEFAULT 0.0,
                    avg_cost      REAL DEFAULT 0.0,
                    current_price REAL DEFAULT 0.0,
                    created_at    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol    TEXT NOT NULL,
                    tx_type   TEXT NOT NULL,
                    quantity  REAL NOT NULL,
                    price_usd REAL NOT NULL,
                    fee_usd   REAL DEFAULT 0.0,
                    exchange  TEXT DEFAULT 'manual',
                    tx_date   TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prices (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT NOT NULL,
                    price_usd   REAL NOT NULL,
                    source      TEXT DEFAULT 'manual',
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tx_symbol    ON transactions(symbol);
                CREATE INDEX IF NOT EXISTS idx_prices_sym   ON prices(symbol, recorded_at);
            """)

    def add_holding(self, symbol: str, name: str) -> Holding:
        """Register a cryptocurrency asset to track."""
        h = Holding(symbol=symbol.upper(), name=name)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO holdings "
                "(symbol, name, total_qty, avg_cost, current_price, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (h.symbol, h.name, h.total_qty, h.avg_cost, h.current_price, h.created_at)
            )
        return h

    def record_transaction(self, symbol: str, tx_type: str, quantity: float,
                           price: float, fee: float = 0.0,
                           exchange: str = "manual") -> Transaction:
        """Record a buy/sell transaction and recalculate holding metrics."""
        symbol = symbol.upper()
        tx = Transaction(symbol=symbol, tx_type=tx_type, quantity=quantity,
                         price_usd=price, fee_usd=fee, exchange=exchange)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO transactions "
                "(symbol, tx_type, quantity, price_usd, fee_usd, exchange, tx_date) "
                "VALUES (?,?,?,?,?,?,?)",
                (tx.symbol, tx.tx_type, tx.quantity, tx.price_usd,
                 tx.fee_usd, tx.exchange, tx.tx_date)
            )
            # Recalculate holding using FIFO cost basis
            rows = conn.execute(
                "SELECT tx_type, quantity, price_usd FROM transactions WHERE symbol=?",
                (symbol,)
            ).fetchall()
            total_qty, total_cost = 0.0, 0.0
            for r in rows:
                if r["tx_type"] == "buy":
                    total_cost += r["quantity"] * r["price_usd"]
                    total_qty  += r["quantity"]
                else:
                    total_qty  -= r["quantity"]
            avg_cost = (total_cost / total_qty) if total_qty > 0 else 0.0
            conn.execute(
                "UPDATE holdings SET total_qty=?, avg_cost=? WHERE symbol=?",
                (max(0.0, total_qty), round(avg_cost, 8), symbol)
            )
        return tx

    def update_price(self, symbol: str, price: float,
                     source: str = "manual") -> PriceRecord:
        """Record latest market price and update holding snapshot."""
        symbol = symbol.upper()
        rec = PriceRecord(symbol=symbol, price_usd=price, source=source)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO prices (symbol, price_usd, source, recorded_at) "
                "VALUES (?,?,?,?)",
                (rec.symbol, rec.price_usd, rec.source, rec.recorded_at)
            )
            conn.execute(
                "UPDATE holdings SET current_price=? WHERE symbol=?",
                (price, symbol)
            )
        return rec

    def get_portfolio(self) -> List[dict]:
        """Return full portfolio with market values and unrealized P&L."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM holdings WHERE total_qty > 0 "
                "ORDER BY (total_qty * current_price) DESC"
            ).fetchall()
        result = []
        for r in rows:
            h = dict(r)
            market_val = h["total_qty"] * h["current_price"]
            cost_basis = h["total_qty"] * h["avg_cost"]
            pnl        = market_val - cost_basis
            pnl_pct    = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
            h.update(market_value=round(market_val, 2),
                     cost_basis=round(cost_basis, 2),
                     unrealized_pnl=round(pnl, 2),
                     pnl_pct=round(pnl_pct, 2))
            result.append(h)
        return result

    def calculate_pnl(self) -> dict:
        """Summarise total portfolio P&L across all positions."""
        portfolio   = self.get_portfolio()
        total_val   = sum(h["market_value"]   for h in portfolio)
        total_cost  = sum(h["cost_basis"]     for h in portfolio)
        total_pnl   = total_val - total_cost
        winners     = [h for h in portfolio if h["unrealized_pnl"] > 0]
        losers      = [h for h in portfolio if h["unrealized_pnl"] < 0]
        pct         = (total_pnl / total_cost * 100) if total_cost else 0.0
        return {
            "total_market_value": f"${total_val:,.2f}",
            "total_cost_basis":   f"${total_cost:,.2f}",
            "unrealized_pnl":     f"${total_pnl:+,.2f}",
            "pnl_percentage":     f"{pct:+.2f}%",
            "winning_positions":  len(winners),
            "losing_positions":   len(losers),
            "total_positions":    len(portfolio),
        }

    def export_report(self, output_path: str = "crypto_report.json") -> str:
        """Export full portfolio report to JSON."""
        with self._conn() as conn:
            txs = [dict(r) for r in conn.execute(
                "SELECT * FROM transactions ORDER BY tx_date DESC LIMIT 200"
            ).fetchall()]
        data = {
            "exported_at":        datetime.now().isoformat(),
            "generator":          "BlackRoad Crypto Tracker v1.0",
            "pnl_summary":        self.calculate_pnl(),
            "portfolio":          self.get_portfolio(),
            "recent_transactions": txs,
        }
        Path(output_path).write_text(json.dumps(data, indent=2))
        return output_path


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _header(title: str):
    w = 64
    print(f"\n{BOLD}{BLUE}{'━' * w}{NC}")
    print(f"{BOLD}{BLUE}  {title}{NC}")
    print(f"{BOLD}{BLUE}{'━' * w}{NC}")


def _pnl_color(pnl: float) -> str:
    return GREEN if pnl >= 0 else RED


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_list(args, tracker: CryptoTracker):
    portfolio = tracker.get_portfolio()
    _header("CRYPTO TRACKER — Portfolio")
    if not portfolio:
        print(f"  {YELLOW}No holdings found. Use 'add' to track assets.{NC}\n")
        return
    total_value = sum(h["market_value"] for h in portfolio)
    for h in portfolio:
        pc   = _pnl_color(h["unrealized_pnl"])
        sign = "+" if h["unrealized_pnl"] >= 0 else ""
        print(f"  {CYAN}{h['symbol']:<8}{NC} {BOLD}{h['name']:<22}{NC}")
        print(f"    Qty:   {h['total_qty']:>16.8f}    "
              f"Avg Cost: {YELLOW}${h['avg_cost']:>12.4f}{NC}    "
              f"Price: {YELLOW}${h['current_price']:>12.4f}{NC}")
        print(f"    Value: {GREEN}${h['market_value']:>14,.2f}{NC}    "
              f"P&L: {pc}{sign}${h['unrealized_pnl']:>10,.2f} "
              f"({sign}{h['pnl_pct']:.2f}%){NC}")
        print()
    print(f"  {BOLD}Total Portfolio Value: {GREEN}${total_value:,.2f}{NC}\n")


def cmd_add(args, tracker: CryptoTracker):
    tracker.add_holding(args.symbol, args.name)
    print(f"\n{GREEN}✓ Asset registered:{NC} "
          f"{BOLD}{args.symbol.upper()}{NC} — {args.name}\n")


def cmd_buy(args, tracker: CryptoTracker):
    tracker.record_transaction(args.symbol, "buy", args.qty, args.price, args.fee)
    total = args.qty * args.price
    print(f"\n{GREEN}✓ BUY recorded{NC}  "
          f"{BOLD}{args.symbol.upper()}{NC}  {args.qty} @ "
          f"${args.price:,.4f} = {YELLOW}${total:,.2f}{NC}\n")


def cmd_sell(args, tracker: CryptoTracker):
    tracker.record_transaction(args.symbol, "sell", args.qty, args.price, args.fee)
    total = args.qty * args.price
    print(f"\n{CYAN}✓ SELL recorded{NC}  "
          f"{BOLD}{args.symbol.upper()}{NC}  {args.qty} @ "
          f"${args.price:,.4f} = {YELLOW}${total:,.2f}{NC}\n")


def cmd_price(args, tracker: CryptoTracker):
    tracker.update_price(args.symbol, args.price, args.source)
    print(f"\n{CYAN}✓ Price updated:{NC} "
          f"{BOLD}{args.symbol.upper()}{NC} = "
          f"{YELLOW}${args.price:,.4f}{NC}  [{args.source}]\n")


def cmd_pnl(args, tracker: CryptoTracker):
    summary = tracker.calculate_pnl()
    _header("CRYPTO PORTFOLIO — P&L SUMMARY")
    for key, val in summary.items():
        label = key.replace("_", " ").title()
        color = GREEN if "+" in str(val) else (RED if "-" in str(val) else CYAN)
        print(f"  {DIM}{label:<25}{NC}  {BOLD}{color}{val}{NC}")
    print()


def cmd_export(args, tracker: CryptoTracker):
    path = tracker.export_report(args.output)
    print(f"\n{GREEN}✓ Report exported to:{NC} {BOLD}{path}{NC}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    tracker = CryptoTracker()
    parser = argparse.ArgumentParser(
        prog="crypto-tracker",
        description=f"{BOLD}BlackRoad Crypto Portfolio Tracker{NC}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s add --symbol BTC --name Bitcoin\n"
            "  %(prog)s buy --symbol BTC --qty 0.5 --price 62000\n"
            "  %(prog)s price --symbol BTC --price 65000\n"
            "  %(prog)s pnl\n"
        ),
    )
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")
    subs.required = True

    subs.add_parser("list", help="Show portfolio holdings")

    p = subs.add_parser("add", help="Track a new crypto asset")
    p.add_argument("--symbol", required=True, metavar="BTC")
    p.add_argument("--name",   required=True, metavar="Bitcoin")

    p = subs.add_parser("buy", help="Record a buy transaction")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty",    required=True, type=float)
    p.add_argument("--price",  required=True, type=float, metavar="USD")
    p.add_argument("--fee",    default=0.0,   type=float)

    p = subs.add_parser("sell", help="Record a sell transaction")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty",    required=True, type=float)
    p.add_argument("--price",  required=True, type=float, metavar="USD")
    p.add_argument("--fee",    default=0.0,   type=float)

    p = subs.add_parser("price", help="Update current market price")
    p.add_argument("--symbol", required=True)
    p.add_argument("--price",  required=True, type=float, metavar="USD")
    p.add_argument("--source", default="manual")

    subs.add_parser("pnl", help="Show P&L summary")

    p = subs.add_parser("export", help="Export portfolio report")
    p.add_argument("--output", default="crypto_report.json", metavar="FILE")

    args = parser.parse_args()
    {"list": cmd_list, "add": cmd_add, "buy": cmd_buy, "sell": cmd_sell,
     "price": cmd_price, "pnl": cmd_pnl, "export": cmd_export
     }[args.command](args, tracker)


if __name__ == "__main__":
    main()
