"""
Research document manager for The Headband.
Loads markdown research docs, parses them, serves via Telegram.
"""
import os
import json
import re
from pathlib import Path

RESEARCH_DIR = Path("/opt/fiery-eyes/research")
INDEX_FILE = RESEARCH_DIR / "index.json"


def load_index() -> dict:
    if INDEX_FILE.exists():
        with open(INDEX_FILE) as f:
            return json.load(f)
    return {}


def save_index(index: dict):
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)


def _find_research_file(token: str) -> Path | None:
    """Find the research markdown file for a token."""
    token_upper = token.upper()
    for fname in os.listdir(RESEARCH_DIR):
        if fname.upper().startswith(token_upper) and fname.endswith('.md') and 'DEPLOY' not in fname.upper():
            return RESEARCH_DIR / fname
    return None


def get_summary_card(token: str) -> str:
    """Generate Telegram summary card for a token."""
    index = load_index()
    token = token.upper()
    if token not in index:
        available = ', '.join(sorted(index.keys()))
        return "No research found for %s. Available: %s" % (token, available)

    info = index[token]

    # Try to extract the TELEGRAM SUMMARY CARD from the markdown file
    filepath = _find_research_file(token)
    if filepath:
        content = filepath.read_text()
        # Find the telegram summary card code block
        match = re.search(r'```\n(.*?)```', content, re.DOTALL)
        if match:
            card = match.group(1).strip()
            # Only use it if it looks like a summary card (has the token name)
            if token in card or '━━━' in card:
                return card

    # Fallback: generate from index
    flags = ""
    if info.get("flags"):
        flags = "\n\u26a0\ufe0f " + ", ".join(info["flags"])

    ev = "EV: %sx" % info['ev_multiple'] if info.get('ev_multiple') else "EV: TBD"
    price = "$%s" % info['price'] if info.get('price') else "TBD"

    return (
        "\u2501\u2501\u2501 %s \u2014 %s/100 \u2501\u2501\u2501\n"
        "%s | %s\n"
        "Recommendation: %s\n"
        "Bear: %s | Base: %s | Bull: %s\n"
        "%s\n"
        "Updated: %s"
        "%s"
    ) % (token, info.get('score', 'TBD'), price, info['rating'],
         info['recommendation'], info.get('bear', 'TBD'),
         info.get('base', 'TBD'), info.get('bull', 'TBD'),
         ev, info['updated'], flags)


def get_full_document_chunks(token: str) -> list:
    """Split full research doc into Telegram-safe chunks (max 3500 chars)."""
    index = load_index()
    token = token.upper()
    if token not in index:
        return ["No research found for %s" % token]

    filepath = _find_research_file(token)
    if not filepath or not filepath.exists():
        return ["Research file not found for %s" % token]

    content = filepath.read_text()

    # Split on ## headers
    sections = re.split(r'\n(?=## )', content)

    chunks = []
    current_chunk = ""

    for section in sections:
        if len(current_chunk) + len(section) > 3500 and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = section
        else:
            current_chunk += "\n\n" + section if current_chunk else section

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Number the chunks
    total = len(chunks)
    return ["[%d/%d]\n%s" % (i + 1, total, chunk) for i, chunk in enumerate(chunks)]


def get_scorecard() -> str:
    """Generate comparative scorecard of all tokens."""
    index = load_index()
    if not index:
        return "No research documents found."

    sorted_tokens = sorted(
        index.items(),
        key=lambda x: x[1].get("score") or 0,
        reverse=True
    )

    lines = ["\u2501\u2501\u2501 THE HEADBAND: WATCHLIST \u2501\u2501\u2501\n"]
    lines.append(" # | Token    | Score | Rating       | Rec")
    lines.append("---|----------|-------|--------------|-------------")

    for i, (token, info) in enumerate(sorted_tokens, 1):
        score = str(info.get('score') or 'TBD').rjust(5)
        rating = (info.get('rating', 'TBD'))[:12].ljust(12)
        rec = (info.get('recommendation', 'TBD'))[:12]
        lines.append("%2d | %-8s | %s | %s | %s" % (i, token, score, rating, rec))

    updated = sorted_tokens[0][1].get('updated', 'N/A') if sorted_tokens else 'N/A'
    lines.append("\nUpdated: %s" % updated)
    lines.append("Use /deepdive [TOKEN] for details")
    lines.append("Use /deepdive [TOKEN] full for complete doc")

    return "\n".join(lines)


def get_price_alerts(current_prices: dict) -> list:
    """Check if any token has moved >10% from research price."""
    index = load_index()
    alerts = []

    for token, info in index.items():
        research_price = info.get('price')
        if not research_price or token not in current_prices:
            continue
        current = current_prices[token].get('price', 0)
        if not current:
            continue
        change = (current - research_price) / research_price * 100
        if abs(change) > 10:
            direction = "\U0001f4c8" if change > 0 else "\U0001f4c9"
            alerts.append("%s %s: %+.1f%% since deep dive ($%.4g -> $%.4g)" % (
                direction, token, change, research_price, current))

    return alerts
