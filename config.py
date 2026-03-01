import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# --- API Keys ---
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ARTEMIS_API_KEY = os.getenv("ARTEMIS_API_KEY", "")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "")
TOKENOMIST_API_KEY = os.getenv("TOKENOMIST_API_KEY", "")
COINANK_API_KEY = os.getenv("COINANK_API_KEY", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")
SMART_MONEY_POLL_ENABLED = bool(GROK_API_KEY)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "fiery-eyes/1.0")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE", str(Path(__file__).parent / "cookies.txt"))
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/fiery_eyes")

# --- KOL Tracking ---
# HELIUS_API_KEY is defined above

# --- Telegram Alpha (Telethon) ---
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
KK_CHANNEL_ID = os.getenv("KK_CHANNEL_ID")

# --- Multi-channel Telegram ---
TELEGRAM_HFIRE_CHAT_ID = os.getenv("TELEGRAM_HFIRE_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))
TELEGRAM_HUOYAN_CHAT_ID = os.getenv("TELEGRAM_HUOYAN_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))
TELEGRAM_SYSTEM_CHAT_ID = os.getenv("TELEGRAM_SYSTEM_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# --- Shadow mode (default ON) ---
SHADOW_MODE = os.getenv("SHADOW_MODE", "true").lower() == "true"

# --- Helius helpers ---
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API_URL = f"https://api.helius.xyz/v1"

# --- Quality Gate thresholds ---
GATE_MAX_SLIPPAGE_PCT = 5.0          # reject if >5% slippage on $10K swap
GATE_MAX_TOP10_PCT = 50.0            # reject if top10 holders >50% supply
GATE_MAX_SYBIL_SCORE = 70            # reject if sybil score >70
GATE_MIN_WALLET_QUALITY = 25         # warn if avg wallet quality <25
GATE_MAX_UNLOCK_VOLUME_RATIO = 3.0   # reject if unlock/volume >3x
GATE_MAX_WASH_SCORE = 70             # reject if wash trading score >70
GATE_MIN_AGE_HOURS = 2               # reject if token <2h old
GATE_MIN_VOLUME_USD = 50_000         # reject if cumulative vol <$50K
GATE_WATCH_MIN_VOLUME_USD = 25_000   # watch tier: min vol $25K

# --- Scanner ---
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))

# --- Logging ---
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        logger.addHandler(console)

        fh = logging.FileHandler(LOG_DIR / "fiery_eyes.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
