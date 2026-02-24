"""Watchlist Manager — JSON-based CRUD for token watchlists.

Supports three watchlist types:
  - adoption: Solana DeFi protocols with growing TVL
  - infrastructure: Established protocol tokens
  - momentum: Auto-populated from Quality Gate passes
"""

import json
from pathlib import Path
from config import get_logger

log = get_logger("watchlists")

WATCHLIST_DIR = Path(__file__).parent
WATCHLIST_FILES = {
    "adoption": WATCHLIST_DIR / "adoption.json",
    "infrastructure": WATCHLIST_DIR / "infrastructure.json",
    "momentum": WATCHLIST_DIR / "momentum.json",
}


def _load_watchlist(name: str) -> dict:
    """Load a watchlist JSON file."""
    path = WATCHLIST_FILES.get(name)
    if not path:
        raise ValueError(f"Unknown watchlist: {name}. Valid: {list(WATCHLIST_FILES.keys())}")

    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {"tokens": []}
    except json.JSONDecodeError:
        log.error("Corrupt watchlist file: %s", path)
        return {"tokens": []}


def _save_watchlist(name: str, data: dict):
    """Save a watchlist JSON file."""
    path = WATCHLIST_FILES.get(name)
    if not path:
        raise ValueError(f"Unknown watchlist: {name}")

    path.write_text(json.dumps(data, indent=2) + "\n")
    log.info("Saved watchlist '%s' (%d tokens)", name, len(data.get("tokens", [])))


def load(name: str) -> list[dict]:
    """Load tokens from a named watchlist.

    Args:
        name: One of 'adoption', 'infrastructure', 'momentum'

    Returns:
        List of token dicts from the watchlist.
    """
    data = _load_watchlist(name)
    return data.get("tokens", [])


def save(name: str, tokens: list[dict]):
    """Save tokens to a named watchlist.

    Args:
        name: One of 'adoption', 'infrastructure', 'momentum'
        tokens: List of token dicts
    """
    data = _load_watchlist(name)
    data["tokens"] = tokens
    _save_watchlist(name, data)


def add_token(name: str, symbol: str, contract: str | None = None,
              coingecko_id: str | None = None, token_name: str | None = None) -> bool:
    """Add a token to a watchlist.

    Returns True if added, False if already exists.
    """
    data = _load_watchlist(name)
    tokens = data.get("tokens", [])

    # Check for duplicates
    for t in tokens:
        if t.get("symbol", "").upper() == symbol.upper():
            log.info("Token %s already in '%s' watchlist", symbol, name)
            return False
        if contract and t.get("contract") == contract:
            log.info("Contract %s already in '%s' watchlist", contract[:16], name)
            return False

    # Build token entry
    entry = {"symbol": symbol.upper(), "name": token_name or symbol.upper()}
    if contract:
        entry["contract"] = contract
    if coingecko_id:
        entry["coingecko_id"] = coingecko_id

    tokens.append(entry)
    data["tokens"] = tokens
    _save_watchlist(name, data)

    log.info("Added %s to '%s' watchlist", symbol, name)
    return True


def remove_token(name: str, symbol: str) -> bool:
    """Remove a token from a watchlist by symbol.

    Returns True if removed, False if not found.
    """
    data = _load_watchlist(name)
    tokens = data.get("tokens", [])

    original_len = len(tokens)
    tokens = [t for t in tokens if t.get("symbol", "").upper() != symbol.upper()]

    if len(tokens) == original_len:
        log.info("Token %s not found in '%s' watchlist", symbol, name)
        return False

    data["tokens"] = tokens
    _save_watchlist(name, data)

    log.info("Removed %s from '%s' watchlist", symbol, name)
    return True


def list_all() -> dict[str, list[dict]]:
    """Get all watchlists and their tokens.

    Returns:
        {"adoption": [...], "infrastructure": [...], "momentum": [...]}
    """
    return {name: load(name) for name in WATCHLIST_FILES}


def get_token(name: str, symbol: str) -> dict | None:
    """Find a specific token in a watchlist by symbol."""
    tokens = load(name)
    for t in tokens:
        if t.get("symbol", "").upper() == symbol.upper():
            return t
    return None


def handle_watch_command(symbol: str, watchlist_name: str = "momentum",
                         contract: str | None = None) -> str:
    """Handle /watch Telegram command.

    Returns formatted response string.
    """
    if watchlist_name not in WATCHLIST_FILES:
        return f"Unknown watchlist: {watchlist_name}. Use: {', '.join(WATCHLIST_FILES.keys())}"

    added = add_token(watchlist_name, symbol, contract=contract)
    if added:
        return f"Added {symbol.upper()} to {watchlist_name} watchlist"
    return f"{symbol.upper()} is already in {watchlist_name} watchlist"


def handle_unwatch_command(symbol: str, watchlist_name: str = "momentum") -> str:
    """Handle /unwatch Telegram command.

    Returns formatted response string.
    """
    if watchlist_name not in WATCHLIST_FILES:
        return f"Unknown watchlist: {watchlist_name}. Use: {', '.join(WATCHLIST_FILES.keys())}"

    removed = remove_token(watchlist_name, symbol)
    if removed:
        return f"Removed {symbol.upper()} from {watchlist_name} watchlist"
    return f"{symbol.upper()} not found in {watchlist_name} watchlist"


def handle_watchlist_command(watchlist_name: str | None = None) -> str:
    """Handle /watchlist Telegram command.

    Args:
        watchlist_name: Specific watchlist to show, or None for all.

    Returns formatted response string.
    """
    if watchlist_name and watchlist_name in WATCHLIST_FILES:
        tokens = load(watchlist_name)
        if not tokens:
            return f"📋 <b>{watchlist_name.title()}</b> watchlist is empty"

        lines = [f"📋 <b>{watchlist_name.title()} Watchlist</b> ({len(tokens)} tokens)"]
        for t in tokens:
            symbol = t.get("symbol", "?")
            name = t.get("name", "")
            contract = t.get("contract", "")
            line = f"  • <b>{symbol}</b>"
            if name and name != symbol:
                line += f" ({name})"
            if contract:
                line += f" <code>{contract[:12]}...</code>"
            lines.append(line)
        return "\n".join(lines)

    # Show all watchlists
    all_lists = list_all()
    lines = ["📋 <b>All Watchlists</b>"]
    for name, tokens in all_lists.items():
        symbols = [t.get("symbol", "?") for t in tokens[:8]]
        preview = ", ".join(symbols)
        if len(tokens) > 8:
            preview += f" +{len(tokens) - 8} more"
        lines.append(f"\n<b>{name.title()}</b> ({len(tokens)} tokens)")
        if preview:
            lines.append(f"  {preview}")
        else:
            lines.append("  (empty)")

    return "\n".join(lines)
