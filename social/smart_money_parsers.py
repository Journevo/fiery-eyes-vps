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

# Bare ALL-CAPS token symbols (no $ prefix): "PYTH is", "bought TAO"
BARE_CAPS_RE = re.compile(r'\b([A-Z][A-Z0-9]{1,9})\b')
# Common English/crypto words that look like tickers but aren't
BARE_CAPS_EXCLUDE = EXCLUDE_SYMBOLS | {
    'THE', 'AND', 'FOR', 'NOT', 'BUT', 'ARE', 'WAS', 'HAS', 'HAD', 'HIS',
    'HER', 'OUR', 'YOU', 'ALL', 'CAN', 'DID', 'GET', 'GOT', 'HAS', 'HIM',
    'HOW', 'ITS', 'LET', 'MAY', 'NEW', 'NOW', 'OLD', 'OWN', 'SAY', 'SHE',
    'TOO', 'USE', 'WAY', 'WHO', 'BOY', 'MAN', 'WIN', 'RUN', 'SET', 'TRY',
    'ASK', 'MEN', 'RAN', 'ANY', 'BAD', 'BIG', 'END', 'FAR', 'FEW', 'GOD',
    'GUY', 'LOW', 'PUT', 'TOP', 'YET', 'API', 'CEO', 'NFT', 'TVL', 'APY',
    'DEX', 'CEX', 'ATH', 'ATL', 'OI', 'PNL', 'ROI', 'USD', 'DCA', 'RWA',
    'ETF', 'IPO', 'CEO', 'CFO', 'CTO', 'COO', 'GDP', 'FED', 'SEC', 'DOJ',
    'FBI', 'CIA', 'USA', 'UAE', 'AI', 'PR', 'TX', 'LP', 'MM', 'YTD', 'QE',
    'GG', 'IMO', 'FWIW', 'DYOR', 'NFA', 'WAGMI', 'NGMI', 'GM', 'GN',
    'JUST', 'FROM', 'THIS', 'THAT', 'WITH', 'HAVE', 'WILL', 'YOUR', 'WHAT',
    'WHEN', 'MAKE', 'LIKE', 'TIME', 'VERY', 'BEEN', 'CALL', 'EACH', 'THAN',
    'THEM', 'THEN', 'SOME', 'MORE', 'ALSO', 'BACK', 'ONLY', 'COME', 'MADE',
    'FIND', 'HERE', 'KNOW', 'TAKE', 'WANT', 'DOES', 'LOOK', 'LONG', 'MUCH',
    'REAL', 'RISK', 'HIGH', 'NEXT', 'BEST', 'LAST', 'OVER', 'SUCH', 'HUGE',
    'LIVE', 'PUMP', 'DUMP', 'FOMO', 'HODL', 'BULL', 'BEAR', 'MOON', 'REKT',
    'SEND', 'SELL', 'HOLD', 'SWAP', 'MINT', 'BURN', 'LOCK', 'DROP', 'FARM',
    'POOL', 'LEND', 'LOAN', 'CCIP', 'WEEK', 'YEAR', 'HALF', 'FULL', 'OPEN',
    'FREE', 'TRUE', 'SAME', 'DONE', 'GONE', 'EVEN', 'SURE', 'ONCE', 'GAVE',
    'TOLD', 'LEFT', 'HARD', 'KEEP', 'STILL', 'THINK', 'EVERY', 'NEVER',
    'START', 'MIGHT', 'WHERE', 'AFTER', 'COULD', 'OTHER', 'ABOUT', 'GREAT',
    'GOING', 'RIGHT', 'BEING', 'WOULD', 'THEIR', 'WHICH', 'THERE', 'THESE',
    'THOSE', 'FIRST', 'UNDER', 'NEEDS', 'HTTPS', 'HTTP', 'ALERT',
}

# Known crypto project names → symbol (lowercase name → uppercase symbol)
# Only includes tokens that are commonly mentioned without $ prefix
KNOWN_TOKEN_NAMES = {
    'sui': 'SUI', 'pyth': 'PYTH', 'ondo': 'ONDO', 'jupiter': 'JUP',
    'jup': 'JUP', 'render': 'RENDER', 'aave': 'AAVE', 'pendle': 'PENDLE',
    'raydium': 'RAY', 'orca': 'ORCA', 'marinade': 'MNDE', 'jito': 'JTO',
    'tensor': 'TNSR', 'drift': 'DRIFT', 'parcl': 'PRCL', 'wormhole': 'W',
    'helium': 'HNT', 'bonk': 'BONK', 'dogwifhat': 'WIF', 'popcat': 'POPCAT',
    'pepe': 'PEPE', 'floki': 'FLOKI', 'shib': 'SHIB', 'doge': 'DOGE',
    'pengu': 'PENGU', 'pudgy': 'PENGU',
    'hyperliquid': 'HYPE', 'tao': 'TAO', 'bittensor': 'TAO',
    'fetch': 'FET', 'near': 'NEAR', 'avalanche': 'AVAX', 'avax': 'AVAX',
    'chainlink': 'LINK', 'polkadot': 'DOT', 'cardano': 'ADA',
    'uniswap': 'UNI', 'aethir': 'ATH_TOKEN', 'virtual': 'VIRTUAL',
    'virtuals': 'VIRTUAL', 'plume': 'PLUME', 'redstone': 'RED',
    'kamino': 'KMNO', 'marginfi': 'MRGN', 'sanctum': 'CLOUD',
    'grass': 'GRASS', 'nosana': 'NOS', 'access': 'ACS',
    'hive': 'HIVE', 'io.net': 'IO', 'shadow': 'SHDW',
}

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
    """Extract token symbols from text via cashtags, bare caps, and known names.

    Priority: $CASHTAG > bare ALLCAPS > known project names.
    """
    symbols = []

    # 1. $CASHTAG (highest confidence)
    for m in SYMBOL_RE.finditer(text):
        sym = m.group(1).upper()
        if sym not in EXCLUDE_SYMBOLS:
            symbols.append(sym)

    # 2. Known project names (e.g. "sui", "pyth", "hyperliquid")
    text_lower = text.lower()
    for name, sym in KNOWN_TOKEN_NAMES.items():
        # Word boundary match — handles "pyth's", "sui,", "sui." etc.
        if re.search(r'\b' + re.escape(name) + r"(?:'s)?\b", text_lower):
            symbols.append(sym)

    # 3. Bare ALL-CAPS words (e.g. "PYTH", "TAO", "SUI")
    for m in BARE_CAPS_RE.finditer(text):
        sym = m.group(1)
        if len(sym) >= 2 and sym not in BARE_CAPS_EXCLUDE:
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


def parse_generic_tweet(tweet_text: str) -> dict:
    """Parse a generic smart money account tweet into a structured signal.

    Uses keyword classification to determine parsed_type and signal_strength.
    Works for any account not handled by a specialized parser.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    sig["wallet_address"] = _extract_solana_address(tweet_text)

    wallet_count = _extract_wallet_count(tweet_text)

    # Keyword classification (priority order)
    if any(kw in text_lower for kw in ("cabal", "insider", "coordinated")):
        sig["parsed_type"] = "cabal_alert"
        sig["extra"] = {"wallet_count": wallet_count, "symbols": symbols}
        sig["signal_strength"] = "strong"

    elif wallet_count >= 3 or "kols bought" in text_lower or "multi" in text_lower:
        sig["parsed_type"] = "multi_kol"
        sig["extra"] = {"wallet_count": wallet_count, "symbols": symbols}
        sig["signal_strength"] = _compute_signal_strength(wallet_count, sig["amount_usd"])

    elif any(kw in text_lower for kw in ("whale", "massive flow", "big buy")):
        sig["parsed_type"] = "whale_flow"
        sig["extra"] = {"wallet_count": wallet_count}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"], True)

    elif any(kw in text_lower for kw in ("accumulat", "loading", "stacking")):
        sig["parsed_type"] = "accumulation"
        sig["extra"] = {"wallet_count": wallet_count}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"])

    elif any(kw in text_lower for kw in ("trending", "hot", "top ")):
        sig["parsed_type"] = "trending"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "medium"

    elif "bought" in text_lower or "buy" in text_lower:
        sig["parsed_type"] = "transaction"
        sig["extra"] = {"action": "buy"}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"])

    elif "sold" in text_lower or "sell" in text_lower:
        sig["parsed_type"] = "transaction"
        sig["extra"] = {"action": "sell"}
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])

    elif any(kw in text_lower for kw in ("launch", "listed", "live on")):
        sig["parsed_type"] = "launch"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "medium"

    else:
        sig["parsed_type"] = "info"
        sig["extra"] = {"symbols": symbols}
        sig["signal_strength"] = "weak"

    return sig


# --- Macro/infra signal categorization ---

# Keywords for macro-level signal classification
_MACRO_KEYWORDS = {
    "rate cut", "rate hike", "rate pause", "fomc", "fed ", "federal reserve",
    "cpi ", "ppi ", "gdp ", "inflation", "recession", "soft landing",
    "hard landing", "quantitative", "liquidity", "money printer",
    "dxy ", "dollar index", "treasury", "yields", "bond",
    "etf approval", "etf filing", "etf inflow", "etf outflow",
    "institutional", "blackrock", "fidelity", "grayscale",
    "macro ", "global liquidity", "risk on", "risk off",
    "tariff", "trade war", "sanctions",
}

_ECOSYSTEM_KEYWORDS = {
    "solana", "sol ecosystem", "sol tvl", "sol dex", "sol defi",
    "jupiter", "jup ", "raydium", "orca ", "marinade", "jito",
    "pump.fun", "pumpfun", "pump fun", "pumpswap",
    "firedancer", "frankendancer", "solana mobile", "saga",
    "solana depin", "helium", "hivemapper", "render",
    "pyth", "wormhole", "drift protocol",
    "sol staking", "validator", "tps ", "solana tps",
    "active addresses", "tvl milestone", "new protocol",
    "chain adoption", "network growth", "developer activity",
}

_RISK_KEYWORDS = {
    "hack", "hacked", "exploit", "exploited", "vulnerability",
    "rug pull", "rugpull", "rugged", "scam ",
    "outage", "down ", "halted", "congestion",
    "sec ", "cftc", "regulatory", "regulation", "lawsuit",
    "depegged", "depeg", "liquidation cascade", "black swan",
    "tether ", "usdt risk", "usdc risk", "bank run",
}

_INFRA_TOKENS = {"SOL", "JUP", "RAY", "ORCA", "MNDE", "JTO", "PYTH", "W",
                 "DRIFT", "TNSR", "PRCL", "HNT", "RENDER", "CLOUD", "IO",
                 "KMNO", "HYPE", "GRASS", "NOS", "SHDW"}


def categorize_signal(tweet_text: str, parsed: dict) -> str:
    """Categorize a parsed X signal into macro theme.

    Returns one of:
        "macro"     — macro narratives (rates, ETF, institutional)
        "ecosystem" — SOL ecosystem developments
        "risk"      — risk alerts (hacks, regulatory, outages)
        "infra"     — infrastructure token mentions (SOL/JUP/etc.)
        "meme"      — individual meme token activity
        "info"      — general / uncategorized
    """
    text_lower = tweet_text.lower()

    # Check risk first (highest priority)
    if any(kw in text_lower for kw in _RISK_KEYWORDS):
        return "risk"

    # Macro narratives
    if any(kw in text_lower for kw in _MACRO_KEYWORDS):
        return "macro"

    # SOL ecosystem developments
    if any(kw in text_lower for kw in _ECOSYSTEM_KEYWORDS):
        return "ecosystem"

    # Infrastructure token mentions
    symbol = parsed.get("token_symbol")
    if symbol and symbol in _INFRA_TOKENS:
        return "infra"

    # Everything else with a token symbol is meme activity
    if symbol:
        return "meme"

    return "info"


# --- Parser dispatch ---

PARSER_MAP = {
    "stalk": parse_stalk_tweet,
    "kolscan": parse_kolscan_tweet,
    "sunflow": parse_sunflow_tweet,
    "gmgn": parse_gmgn_tweet,
    "generic": parse_generic_tweet,
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

    # Test symbol extraction — cashtags
    syms = _extract_symbols("$BONK and $WIF are hot, also $USDT")
    assert syms == ["BONK", "WIF"], f"Expected ['BONK', 'WIF'], got {syms}"
    print("  Symbol extraction (cashtags): OK")

    # Test symbol extraction — bare ALLCAPS
    syms2 = _extract_symbols("PYTH is the oracle layer, TAO has ETF catalysts")
    assert "PYTH" in syms2, f"Expected PYTH in {syms2}"
    assert "TAO" in syms2, f"Expected TAO in {syms2}"
    print("  Symbol extraction (bare caps): OK")

    # Test symbol extraction — known project names
    syms3 = _extract_symbols("sui down 83% from ath but tvl hit $1B")
    assert "SUI" in syms3, f"Expected SUI in {syms3}"
    print("  Symbol extraction (known names): OK")

    # Test symbol extraction — possessive form
    syms4 = _extract_symbols("pyth's pull model and chainlink dominates")
    assert "PYTH" in syms4, f"Expected PYTH in {syms4}"
    assert "LINK" in syms4, f"Expected LINK in {syms4}"
    print("  Symbol extraction (possessive): OK")

    # Test generic parser — cabal
    sample_g1 = "ALERT: Insider cabal detected on $DOGE — 15 wallets coordinated"
    result_g1 = parse_generic_tweet(sample_g1)
    assert result_g1["parsed_type"] == "cabal_alert"
    assert result_g1["signal_strength"] == "strong"
    print("  Generic cabal parser: OK")

    # Test generic parser — accumulation
    sample_g2 = "Smart wallets accumulating $POPCAT — $25K total bought"
    result_g2 = parse_generic_tweet(sample_g2)
    assert result_g2["parsed_type"] == "accumulation"
    assert result_g2["token_symbol"] == "POPCAT"
    print("  Generic accumulation parser: OK")

    # Test generic parser — trending
    sample_g3 = "Trending: $BONK, $WIF are hot right now"
    result_g3 = parse_generic_tweet(sample_g3)
    assert result_g3["parsed_type"] == "trending"
    assert result_g3["signal_strength"] == "medium"
    print("  Generic trending parser: OK")

    print("\nAll parser tests passed!")


# --- NEW Tier 1 Parsers (Task 8 — v5.1) ---

def parse_lookonchain_tweet(tweet_text: str) -> dict:
    """Parse a @lookonchain tweet into a structured signal.

    Lookonchain posts contextualised whale analysis: WHO is buying + WHY.
    Format often includes: entity name, amount, token, context.
    Examples:
      "A whale bought 1,000 ETH ($3.5M) from Binance 2 hours ago"
      "Justin Sun deposited $50M USDT to Binance"
      "A smart money wallet accumulated 2M $JUP ($340K) in the past 3 days"
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    sig["wallet_address"] = _extract_solana_address(tweet_text)

    wallet_count = _extract_wallet_count(tweet_text)

    # Detect direction
    buy_kw = ("bought", "accumulated", "withdrew", "receiving", "acquired", "added")
    sell_kw = ("sold", "deposited to", "transferred to binance", "transferred to coinbase",
               "dumped", "selling")

    is_buy = any(kw in text_lower for kw in buy_kw)
    is_sell = any(kw in text_lower for kw in sell_kw)

    # Detect entity (Lookonchain often names who)
    entity = None
    entity_patterns = [
        r'(?:a |the )?(?:whale|smart money|institution|fund|wallet)',
        r'(?:justin sun|do kwon|vitalik|satoshi|sbf|cz|brian armstrong)',
        r'(?:blackrock|fidelity|grayscale|ark invest|galaxy digital)',
        r'(?:binance|coinbase|kraken|okx|bybit)',
    ]
    for pat in entity_patterns:
        m = re.search(pat, text_lower)
        if m:
            entity = m.group(0).strip()
            break

    if is_buy and not is_sell:
        sig["parsed_type"] = "whale_buy"
        sig["extra"] = {"direction": "buy", "entity": entity, "symbols": symbols}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"],
            "whale" in text_lower or (sig["amount_usd"] and sig["amount_usd"] >= 100_000))
    elif is_sell:
        sig["parsed_type"] = "whale_sell"
        sig["extra"] = {"direction": "sell", "entity": entity, "symbols": symbols}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"],
            sig["amount_usd"] and sig["amount_usd"] >= 100_000)
    elif any(kw in text_lower for kw in ("exchange outflow", "outflow")):
        sig["parsed_type"] = "exchange_flow"
        sig["extra"] = {"direction": "outflow", "entity": entity}
        sig["signal_strength"] = "medium"
    elif any(kw in text_lower for kw in ("exchange inflow", "inflow")):
        sig["parsed_type"] = "exchange_flow"
        sig["extra"] = {"direction": "inflow", "entity": entity}
        sig["signal_strength"] = "medium"
    else:
        sig["parsed_type"] = "whale_activity"
        sig["extra"] = {"entity": entity, "symbols": symbols}
        sig["signal_strength"] = _compute_signal_strength(
            wallet_count, sig["amount_usd"])

    return sig


def parse_moby_tweet(tweet_text: str) -> dict:
    """Parse a @whalewatchalert (Moby) tweet into a structured signal.

    Moby tracks Solana whale alerts with PnL tracking.
    Shows trader PROFITABILITY — which is the key differentiator.
    Examples:
      "🐋 Whale bought $500K of $JUP — Wallet PnL: +340% (30d)"
      "Top trader sold 50% of $BONK position — realized +$120K profit"
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    sig["wallet_address"] = _extract_solana_address(tweet_text)

    # Extract PnL data (key Moby differentiator)
    pnl_match = re.search(r'[Pp][Nn][Ll]:?\s*([+-]?\d+(?:\.\d+)?)\s*%', tweet_text)
    pnl_pct = float(pnl_match.group(1)) if pnl_match else None

    profit_match = re.search(r'(?:profit|gain|loss):?\s*\$?([0-9,]+(?:\.[0-9]+)?)\s*([KkMm])?', tweet_text, re.I)
    realized_pnl = None
    if profit_match:
        realized_pnl = float(profit_match.group(1).replace(',', ''))
        suffix = (profit_match.group(2) or '').upper()
        if suffix == 'K':
            realized_pnl *= 1_000
        elif suffix == 'M':
            realized_pnl *= 1_000_000

    # Determine wallet quality from PnL
    wallet_quality = None
    if pnl_pct is not None:
        if pnl_pct >= 100:
            wallet_quality = "elite"
        elif pnl_pct >= 30:
            wallet_quality = "profitable"
        elif pnl_pct >= 0:
            wallet_quality = "breakeven"
        else:
            wallet_quality = "underwater"

    # Direction detection
    is_buy = any(kw in text_lower for kw in ("bought", "buy", "accumulated", "added"))
    is_sell = any(kw in text_lower for kw in ("sold", "sell", "dumped", "reduced", "exited"))

    if is_buy:
        sig["parsed_type"] = "whale_buy"
        # Strong if high-PnL trader is buying
        if wallet_quality in ("elite", "profitable"):
            sig["signal_strength"] = "strong"
        else:
            sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"],
                                                               "whale" in text_lower)
    elif is_sell:
        sig["parsed_type"] = "whale_sell"
        sig["signal_strength"] = _compute_signal_strength(0, sig["amount_usd"])
    else:
        sig["parsed_type"] = "whale_activity"
        sig["signal_strength"] = "medium"

    sig["extra"] = {
        "pnl_pct": pnl_pct,
        "realized_pnl": realized_pnl,
        "wallet_quality": wallet_quality,
        "direction": "buy" if is_buy else ("sell" if is_sell else "unknown"),
        "symbols": symbols,
    }

    return sig


def parse_aixbt_tweet(tweet_text: str) -> dict:
    """Parse an @aixbt_agent tweet into a structured signal.

    aixbt posts conversational analysis mentioning tokens naturally.
    Extract: token symbols, dollar amounts, sentiment, key metrics.
    """
    sig = _base_signal()
    text_lower = tweet_text.lower()

    # Extract all tokens mentioned
    symbols = _extract_symbols(tweet_text)
    sig["token_symbol"] = symbols[0] if symbols else None
    sig["token_address"] = _extract_solana_address(tweet_text)
    sig["amount_usd"] = _extract_usd_amount(tweet_text)
    wallet_count = _extract_wallet_count(tweet_text)

    # Classify the tweet content
    buy_kw = ("smart money", "accumulating", "inflow", "bought", "holding", "bullish",
              "revenue", "growing", "partnership", "upgrade")
    sell_kw = ("sold", "outflow", "dumped", "vulnerability", "exploit", "hack",
               "bearish", "declining", "risk")

    buy_score = sum(1 for kw in buy_kw if kw in text_lower)
    sell_score = sum(1 for kw in sell_kw if kw in text_lower)

    if buy_score > sell_score and buy_score >= 2:
        sig["parsed_type"] = "bullish_analysis"
        sig["signal_strength"] = _compute_signal_strength(wallet_count, sig["amount_usd"],
                                                           "smart money" in text_lower)
    elif sell_score > buy_score and sell_score >= 2:
        sig["parsed_type"] = "bearish_analysis"
        sig["signal_strength"] = _compute_signal_strength(wallet_count, sig["amount_usd"],
                                                           "exploit" in text_lower or "hack" in text_lower)
    elif sig["amount_usd"] and sig["amount_usd"] >= 100_000:
        sig["parsed_type"] = "market_data"
        sig["signal_strength"] = "medium"
    elif symbols:
        sig["parsed_type"] = "token_mention"
        sig["signal_strength"] = "weak"
    else:
        sig["parsed_type"] = "commentary"
        sig["signal_strength"] = "weak"

    sig["extra"] = {
        "all_symbols": symbols,
        "buy_indicators": buy_score,
        "sell_indicators": sell_score,
        "source": "aixbt",
    }

    return sig


# Update PARSER_MAP with new parsers
PARSER_MAP["lookonchain"] = parse_lookonchain_tweet
PARSER_MAP["moby"] = parse_moby_tweet
PARSER_MAP["aixbt"] = parse_aixbt_tweet
