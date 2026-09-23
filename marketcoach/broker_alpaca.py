"""Minimal Alpaca client (pure stdlib) for PAPER trading crypto.

Talks to Alpaca's paper endpoint only. If no API keys are configured it runs in
DRY-RUN: it logs the orders it *would* place instead of sending them, so the whole
pipeline is testable before you sign up. Drop keys in and it goes live (paper).

Keys are read from env (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY) or from
paper_state/alpaca.keys.json = {"key_id": "...", "secret_key": "..."} (gitignored).

SAFETY: base URL is hard-pinned to the PAPER host. This module never touches the
live-money endpoint. Going to real money is a separate, deliberate change.
"""
import json, os, urllib.request, urllib.parse, urllib.error

PAPER_TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "alpaca.keys.json")


def load_keys():
    kid = os.environ.get("ALPACA_API_KEY_ID")
    sec = os.environ.get("ALPACA_API_SECRET_KEY")
    if kid and sec:
        return kid, sec
    if os.path.exists(KEYS_FILE):
        try:
            d = json.load(open(KEYS_FILE))
            return d.get("key_id"), d.get("secret_key")
        except Exception:
            pass
    return None, None


def to_alpaca_symbol(product):
    """'BTC-USD' (our/Coinbase form) -> 'BTC/USD' (Alpaca crypto form)."""
    return product.replace("-USD", "/USD").replace("-", "/")


class Alpaca:
    def __init__(self, dry_run=None):
        self.key_id, self.secret = load_keys()
        self.live = bool(self.key_id and self.secret)
        self.dry_run = (not self.live) if dry_run is None else dry_run

    # --- low-level ---
    def _req(self, method, base, path, body=None):
        url = base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key_id or "",
            "APCA-API-SECRET-KEY": self.secret or "",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            raise RuntimeError(f"Alpaca {method} {path} -> HTTP {e.code}: {detail}")

    # --- account / positions ---
    def account(self):
        if self.dry_run:
            return {"status": "DRY_RUN", "equity": None, "cash": None}
        return self._req("GET", PAPER_TRADING, "/v2/account")

    def positions(self):
        if self.dry_run:
            return []
        return self._req("GET", PAPER_TRADING, "/v2/positions")

    def position_qty(self, product):
        sym = to_alpaca_symbol(product)
        for p in self.positions():
            if p.get("symbol") in (sym, product.replace("-", "")):
                return float(p.get("qty", 0)), float(p.get("avg_entry_price", 0))
        return 0.0, 0.0

    # --- orders ---
    def buy_notional(self, product, dollars):
        sym = to_alpaca_symbol(product)
        if self.dry_run:
            print(f"    [DRY] BUY ${dollars:.2f} of {sym} (market)")
            return {"dry_run": True, "side": "buy", "symbol": sym, "notional": dollars}
        return self._req("POST", PAPER_TRADING, "/v2/orders", {
            "symbol": sym, "notional": f"{dollars:.2f}", "side": "buy",
            "type": "market", "time_in_force": "gtc"})

    def close(self, product):
        sym = to_alpaca_symbol(product)
        if self.dry_run:
            print(f"    [DRY] CLOSE position {sym}")
            return {"dry_run": True, "side": "close", "symbol": sym}
        enc = urllib.parse.quote(sym, safe="")
        return self._req("DELETE", PAPER_TRADING, f"/v2/positions/{enc}")

    def tradable_crypto(self):
        """Set of tradable crypto symbols ('BTC/USD', ...). Empty in dry-run."""
        if self.dry_run:
            return set()
        assets = self._req("GET", PAPER_TRADING, "/v2/assets?asset_class=crypto&status=active")
        return {a["symbol"] for a in assets if a.get("tradable")}
