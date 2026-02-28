"""Smart money tweet parsers — one per X account.

Each parser takes raw tweet text and returns a structured signal dict:
    {
        "parsed_type": str,            # accumulation | transaction | whale_flow | trending | ranking | ...
        "token_symbol": str | None,
        "token_address": str | None,   # Solana base58 address if found
        "wallet_address": str | None,
        "amount_usd": float | None,
        "signal_strength": str,        # weak | medium | strong
        "extra": dict,                 # parser-specific fields
    }
"""

import re

# --- Shared regex patterns ---

# Solana contract address (base58, 32-44 chars, no leading 0/O/I/l)
SOLANA_ADDR_RE = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

# Cashtag symbol ($TOKEN)
SYMBOL_RE = re.compile(r'\$([A-Za-z][A-Za-z0-9]{1,14})\b')
EXCLUDE_SYMBOLS = {'USD', 'SOL', 'BTC', 'ETH', 'USDT', 'USDC', 'BNB',
                   'BUSD', 'DAI', 'WETH', 'WSOL', 'ARB', 'OP'}

# Dollar amount: $1,234 or $1.2K or $50K or $1.2M
USD_PLAIN_RE = re.compile(r'\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\b')
USD_SUFFIX_RE = re.compile(r'\$([0-9]+(?:\.[0-9]+)?)\s*([KkMmBb])\b')

# Wallet/KOL count: "12 wallets", "5 KOLs"
WALLET_COUNT_RE = re.compile(r'(\d+)\s+(?:wallets?|KOLs?|traders?|whales?|addresses)', re.I)

# Percentage
PCT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%')


def _extract_solana_address(text: str) -> str | None:
    """Extract first Solana contract address from text."""
    match = SOLANA_ADDR_RE.search(text)
    return match.group(0) if match else None


def _extract_usd_amount(text: str) -> float | None:
    """Extract the largest USD dollar amount from text."""
    amounts = []

    # Plain format: $1,234
    for m in USD_PLAIN_RE.finditer(text):
        val = float(m.group(1).replace(',', ''))
        amounts.append(val)

    # Suffix format: $1.2K, $50M
    for m in USD_SUFFIX_RE.finditer(text):
        val = float(m.group(1))
        suffix = m.group(2).upper()
        if suffix == 'K':
            val *= 1_000
        elif suffix == 'M':
            val *= 1_000_000
        elif suffix == 'B':
            val *= 1_000_000_000
        amounts.append(val)

    return max(amounts) if amounts else None


def _extract_symbols(text: str) -> list[str]:
    """Extract all $SYMBOL cashtag references from text."""
    symbols = []
    for m in SYMBOL_RE.finditer(text):
        sym = m.group(1).upper()
        if sym not in EXCLUDE_SYMBOLS:
            symbols.append(sym)
    # Deduplicate preserving order
    seen = set()
    result = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _extract_wallet_count(text: str) -> int:
    """Extract wallet/KOL count from text."""
    m = WALLET_COUNT_RE.search(text)
    return int(m.group(1)) if m else 0


def _compute_signal_strength(wallet_count: int = 0,
                              amount_usd: float | None = None,
                              has_whale_keyword: bool = False) -> str:
    """Determine signal strength from available evidence."""
    if has_whale_keyword or wallet_count >= 10 or (amount_usd and amount_usd >= 100_000):
        return "strong"
    if wallet_count >= 3 or (amount_usd and amount_usd >= 10_000):
        return "medium"
    return "weak"


def _base_signal() -> dict:
    """Return a base signal dict with all fields set to defaults."""
    return {
        "parsed_type": "unknown",
        "token_symbol": None,
        "token_address": None,
        "wallet_address": None,
        "amount_usd": None,
        "signal_strength": "weak",
        "extra": {},
    }


# --- Per-account parsers ---

def parse_stalk_tweet(tweet_text: str) -> dict:
    """Parse a @StalkHQ tweet into a structured signal.

    Detects: accumulation alerts, cabal alerts, daily most-bought/sold rankings.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)

    wallet_count = _extract_wallet_count(tweet_text)

    if "most bought" in text_lower or "most sold" in text_lower or "ranking" in text_lower:
        sig["parsed_type"] = "ranking"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "medium" if len(symbols) >= 3 else "weak"
    elif "cabal" in text_lower:
        sig["parsed_type"] = "cabal_alert"
        sig["extra"] = {"wallet_count": wallet_count, "symbols": symbols}
        sig["signal_strength"] = "strong"
    elif any(kw in text_lower for kw in ("accumulat", "bought", "buying", "loading")):
        sig["parsed_type"] = "accumulation"
        sig["extra"] = {"wallet_count": wallet_count}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"],
            "whale" in text_lower or "massive" in text_lower)
    else:
        sig["parsed_type"] = "info"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "weak"

    return sig


def parse_kolscan_tweet(tweet_text: str) -> dict:
    """Parse a @kolscan_io tweet into a structured signal.

    Detects: real-time KOL transactions, PnL leaderboards, multi-KOL buys.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    sig["wallet_address"] = _extract_solana_address(tweet_text)

    wallet_count = _extract_wallet_count(tweet_text)

    if "leaderboard" in text_lower or "pnl" in text_lower or "top" in text_lower:
        sig["parsed_type"] = "leaderboard"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "weak"
    elif wallet_count >= 2 or "kols bought" in text_lower:
        sig["parsed_type"] = "multi_kol_buy"
        sig["extra"] = {"wallet_count": wallet_count, "symbols": symbols}
        sig["signal_strength"] = _compute_signal_strength(wallet_count, sig["amount_usd"])
    elif "bought" in text_lower or "buy" in text_lower:
        sig["parsed_type"] = "transaction"
        sig["extra"] = {"action": "buy"}
        sig["signal_strength"] = _compute_signal_strength(1, sig["amount_usd"])
    elif "sold" in text_lower or "sell" in text_lower:
        sig["parsed_type"] = "transaction"
        sig["extra"] = {"action": "sell"}
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])
    else:
        sig["parsed_type"] = "info"
        sig["signal_strength"] = "weak"

    return sig


def parse_sunflow_tweet(tweet_text: str) -> dict:
    """Parse a @SunFlowSolana tweet into a structured signal.

    Detects: whale flow alerts, entry timing signals, DCA tracking patterns.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    sig["wallet_address"] = _extract_solana_address(tweet_text)

    has_whale = "whale" in text_lower
    pct_match = PCT_RE.search(tweet_text)
    dca_pct = float(pct_match.group(1)) if pct_match else None

    if has_whale and ("alert" in text_lower or "massive" in text_lower):
        sig["parsed_type"] = "whale_flow"
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"], True)
    elif "dca" in text_lower or "averaging" in text_lower:
        sig["parsed_type"] = "dca_entry"
        sig["extra"] = {"dca_pct": dca_pct}
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])
    elif has_whale or "entry" in text_lower or "bought" in text_lower:
        sig["parsed_type"] = "whale_flow"
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])
    else:
        sig["parsed_type"] = "info"
        sig["signal_strength"] = "weak"

    return sig


def parse_gmgn_tweet(tweet_text: str) -> dict:
    """Parse a @gmaborabot tweet into a structured signal.

    Detects: smart money trending, top traders, whale alerts.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)

    if "trending" in text_lower or "hot" in text_lower:
        sig["parsed_type"] = "trending"
        sig["extra"] = {"trending_symbols": symbols}
        sig["signal_strength"] = "medium" if len(symbols) >= 3 else "weak"
    elif "top trader" in text_lower:
        sig["parsed_type"] = "top_traders"
        sig["wallet_address"] = _extract_solana_address(tweet_text)
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "medium"
    elif "whale" in text_lower:
        sig["parsed_type"] = "whale_alert"
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"], True)
    elif "bought" in text_lower or "buy" in text_lower:
        sig["parsed_type"] = "transaction"
        sig["extra"] = {"action": "buy"}
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])
    else:
        sig["parsed_type"] = "info"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "weak"

    return sig


# --- Parser dispatch ---

PARSER_MAP = {
    "stalk": parse_stalk_tweet,
    "kolscan": parse_kolscan_tweet,
    "sunflow": parse_sunflow_tweet,
    "gmgn": parse_gmgn_tweet,
}


# --- Self-test ---

if __name__ == "__main__":
    # Test StalkHQ parser
    sample = ("ACCUMULATION ALERT\n12 smart wallets accumulated $BONK\n"
              "Contract: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263\n"
              "Avg position: $4,200 | Total: $50,400\n"
              "Wallets started buying: 2h ago")
    result = parse_stalk_tweet(sample)
    assert result["parsed_type"] == "accumulation", f"Expected accumulation, got {result['parsed_type']}"
    assert result["token_symbol"] == "BONK", f"Expected BONK, got {result['token_symbol']}"
    assert result["token_address"] is not None, "Expected token address"
    assert result["amount_usd"] == 50_400.0, f"Expected 50400, got {result['amount_usd']}"
    assert result["signal_strength"] == "strong", f"Expected strong, got {result['signal_strength']}"
    print("  StalkHQ accumulation parser: OK")

    # Test StalkHQ ranking
    sample2 = "Most bought today: $BONK, $WIF, $POPCAT | Most sold: $BOME, $MYRO"
    result2 = parse_stalk_tweet(sample2)
    assert result2["parsed_type"] == "ranking"
    assert len(result2["extra"]["symbols"]) >= 3
    print("  StalkHQ ranking parser: OK")

    # Test KOLScan parser
    sample3 = ("KOL ansem.sol bought $WIF\n"
               "Address: EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm\n"
               "$15,000 on pump.fun")
    result3 = parse_kolscan_tweet(sample3)
    assert result3["parsed_type"] == "transaction"
    assert result3["token_symbol"] == "WIF"
    assert result3["amount_usd"] == 15_000.0
    assert result3["signal_strength"] == "medium"
    print("  KOLScan transaction parser: OK")

    # Test multi-KOL buy
    sample3b = "3 KOLs bought $POPCAT in the last 30 minutes — $45K total"
    result3b = parse_kolscan_tweet(sample3b)
    assert result3b["parsed_type"] == "multi_kol_buy"
    assert result3b["signal_strength"] == "medium"
    print("  KOLScan multi-KOL parser: OK")

    # Test SunFlow parser
    sample4 = ("WHALE ALERT: Wallet 7GYcUW... bought $6,535 of $PIGEON\n"
               "At $0.001 (MCap $1.5M)\n"
               "DCA 15% into position")
    result4 = parse_sunflow_tweet(sample4)
    assert result4["parsed_type"] == "whale_flow"
    assert result4["token_symbol"] == "PIGEON"
    assert result4["signal_strength"] == "strong"
    print("  SunFlow whale flow parser: OK")

    # Test SunFlow DCA
    sample4b = "Whale DCA'd into $BONK, averaging down 20% — position now $120K total"
    result4b = parse_sunflow_tweet(sample4b)
    assert result4b["parsed_type"] == "dca_entry"
    assert result4b["amount_usd"] == 120_000.0
    print("  SunFlow DCA parser: OK")

    # Test GMGN parser
    sample5 = "Trending among smart money: $BONK, $WIF, $POPCAT, $MYRO"
    result5 = parse_gmgn_tweet(sample5)
    assert result5["parsed_type"] == "trending"
    assert len(result5["extra"]["trending_symbols"]) >= 3
    assert result5["signal_strength"] == "medium"
    print("  GMGN trending parser: OK")

    # Test USD extraction
    assert _extract_usd_amount("bought $1.2M worth") == 1_200_000.0
    assert _extract_usd_amount("total $50K invested") == 50_000.0
    assert _extract_usd_amount("avg $4,200 per wallet") == 4_200.0
    assert _extract_usd_amount("no money mentioned") is None
    print("  USD extraction: OK")

    # Test symbol extraction
    syms = _extract_symbols("$BONK and $WIF are hot, also $USDT")
    assert syms == ["BONK", "WIF"], f"Expected ['BONK', 'WIF'], got {syms}"
    print("  Symbol extraction: OK")

    print("\nAll parser tests passed!")
