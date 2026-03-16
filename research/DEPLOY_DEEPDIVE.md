# CLAUDE CODE TASK: Deploy /deepdive Research System
# Priority: HIGH — all research content is ready, just needs wiring

## WHAT TO BUILD

The Headband research library — deep dive documents for 12 tokens,
served via Telegram /deepdive command.

## STEP 1: Create directory and upload research files

```bash
mkdir -p /opt/fiery-eyes/research
```

The research files will be uploaded separately via scp.
They follow this naming: {TOKEN}_research_v1.md

## STEP 2: Create index.json

Create /opt/fiery-eyes/research/index.json with this content:

```json
{
  "BTC": {"score": 90, "rating": "EXCEPTIONAL", "recommendation": "ACCUMULATE", "price": 70800, "ev_multiple": 2.85, "bear": "$35-55K", "base": "$200-250K", "bull": "$350K", "updated": "2026-03-15"},
  "SOL": {"score": 82, "rating": "STRONG", "recommendation": "ACCUMULATE", "price": 88.50, "ev_multiple": 4.9, "bear": "$40-55", "base": "$400-500", "bull": "$800", "updated": "2026-03-15"},
  "HYPE": {"score": 79, "rating": "STRONG", "recommendation": "ACCUMULATE", "price": 37.90, "ev_multiple": 3.1, "bear": "$12-18", "base": "$100-150", "bull": "$300", "updated": "2026-03-15"},
  "JUP": {"score": 78, "rating": "STRONG", "recommendation": "ACCUMULATE", "price": 0.16, "ev_multiple": 12.7, "bear": "$0.05-0.10", "base": "$1.50-3.00", "bull": "$6", "updated": "2026-03-15"},
  "RENDER": {"score": 72, "rating": "WATCHLIST", "recommendation": "WATCH", "price": 1.87, "ev_multiple": 6.5, "bear": "$0.50-1.00", "base": "$10-14", "bull": "$35", "flags": ["correlation trap"], "updated": "2026-03-15"},
  "SUI": {"score": 71, "rating": "WATCHLIST", "recommendation": "WATCH", "price": 1.01, "ev_multiple": 6.6, "bear": "$0.40-0.60", "base": "$5-8", "bull": "$18", "flags": ["61% locked supply"], "updated": "2026-03-15"},
  "PUMP": {"score": 55, "rating": "SPECULATIVE", "recommendation": "SMALL_POSITION", "price": 0.002, "ev_multiple": 6.5, "bear": "zero risk", "base": "$0.005-0.01", "bull": "$0.02", "flags": ["team selling"], "updated": "2026-03-15"},
  "BONK": {"score": 52, "rating": "SPECULATIVE", "recommendation": "SMALL_POSITION", "price": 0.0000061, "ev_multiple": 4.6, "bear": "$0.000002", "base": "$0.00002-4", "bull": "$0.00006", "updated": "2026-03-15"},
  "USELESS": {"score": 45, "rating": "PROVISIONAL", "recommendation": "HOLD_ONLY", "price": null, "ev_multiple": null, "flags": ["insufficient data"], "updated": "2026-03-15"},
  "PENGU": {"score": 44, "rating": "SPECULATIVE", "recommendation": "MOONBAG_ONLY", "price": 0.004, "ev_multiple": 3.5, "bear": "$0.001", "base": "$0.01-0.02", "bull": "$0.05", "updated": "2026-03-15"},
  "FARTCOIN": {"score": 25, "rating": "AVOID", "recommendation": "JINGUBANG_ONLY", "price": 0.25, "ev_multiple": null, "flags": ["NCRA only"], "updated": "2026-03-15"},
  "DEEP": {"score": null, "rating": "TBD", "recommendation": "RESEARCH_NEEDED", "price": null, "ev_multiple": null, "updated": "2026-03-15"}
}
```

## STEP 3: Build research_manager.py

Create /opt/fiery-eyes/research/research_manager.py:

```python
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

def get_summary_card(token: str) -> str:
    """Generate Telegram summary card for a token."""
    index = load_index()
    token = token.upper()
    if token not in index:
        available = ', '.join(sorted(index.keys()))
        return f"No research found for {token}. Available: {available}"
    
    info = index[token]
    
    # Try to extract the TELEGRAM SUMMARY CARD from the markdown file
    for fname in os.listdir(RESEARCH_DIR):
        if fname.upper().startswith(token) and fname.endswith('.md'):
            filepath = RESEARCH_DIR / fname
            content = filepath.read_text()
            # Find the telegram summary card code block
            match = re.search(r'```\n(===.*?)```', content, re.DOTALL)
            if match:
                return match.group(1).strip()
    
    # Fallback: generate from index
    flags = ""
    if info.get("flags"):
        flags = "\n⚠️ " + ", ".join(info["flags"])
    
    ev = f"EV: {info['ev_multiple']}x" if info.get('ev_multiple') else "EV: TBD"
    price = f"${info['price']}" if info.get('price') else "TBD"
    
    return (
        f"=== {token} — {info.get('score', 'TBD')}/100 ===\n"
        f"{price} | {info['rating']}\n"
        f"Recommendation: {info['recommendation']}\n"
        f"Bear: {info.get('bear', 'TBD')} | Base: {info.get('base', 'TBD')} | Bull: {info.get('bull', 'TBD')}\n"
        f"{ev}\n"
        f"Updated: {info['updated']}"
        f"{flags}"
    )


def get_full_document_chunks(token: str) -> list:
    """Split full research doc into Telegram-safe chunks (max 3500 chars)."""
    index = load_index()
    token = token.upper()
    if token not in index:
        return [f"No research found for {token}"]
    
    # Find the markdown file
    filepath = None
    for fname in os.listdir(RESEARCH_DIR):
        if fname.upper().startswith(token) and fname.endswith('.md'):
            filepath = RESEARCH_DIR / fname
            break
    
    if not filepath or not filepath.exists():
        return [f"Research file not found for {token}"]
    
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
    return [f"[{i+1}/{total}]\n{chunk}" for i, chunk in enumerate(chunks)]


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
    
    lines = ["=== THE HEADBAND: WATCHLIST ===\n"]
    lines.append(" # │ Token    │ Score │ Rating       │ EV")
    lines.append("───┼──────────┼───────┼──────────────┼────────")
    
    for i, (token, info) in enumerate(sorted_tokens, 1):
        score = str(info.get('score', 'TBD')).rjust(5)
        rating = (info.get('rating', 'TBD'))[:12].ljust(12)
        ev = f"{info['ev_multiple']}x" if info.get('ev_multiple') else "TBD"
        lines.append(f"{i:2d} │ {token:<8s} │ {score} │ {rating} │ {ev}")
    
    updated = sorted_tokens[0][1].get('updated', 'N/A') if sorted_tokens else 'N/A'
    lines.append(f"\nUpdated: {updated}")
    lines.append("Use /deepdive [TOKEN] for details")
    lines.append("Use /deepdive [TOKEN] full for complete doc")
    
    return "\n".join(lines)
```

## STEP 4: Wire /deepdive command into Telegram bot

Add to v5_bot.py:

```python
# At top with other imports
from research.research_manager import get_summary_card, get_full_document_chunks, get_scorecard

# In the command registration section (where other CommandHandlers are added):
application.add_handler(CommandHandler("deepdive", handle_deepdive))

# The handler function:
async def handle_deepdive(update, context):
    """Handle /deepdive command for research documents."""
    args = context.args if context.args else []
    
    if not args:
        await update.message.reply_text(
            "=== THE HEADBAND ===\n\n"
            "Usage:\n"
            "/deepdive all — Scorecard\n"
            "/deepdive BTC — Summary card\n"
            "/deepdive BTC full — Full document\n"
        )
        return
    
    token = args[0].upper()
    
    if token == "ALL":
        card = get_scorecard()
        await update.message.reply_text(card)
        return
    
    if len(args) > 1 and args[1].lower() == "full":
        chunks = get_full_document_chunks(token)
        for chunk in chunks:
            await update.message.reply_text(chunk)
            await asyncio.sleep(0.5)
        return
    
    # Default: summary card
    card = get_summary_card(token)
    await update.message.reply_text(card)
```

## STEP 5: Add __init__.py for research module

```bash
touch /opt/fiery-eyes/research/__init__.py
```

## STEP 6: Test

```bash
# Restart service
systemctl restart fiery-eyes-v5.service

# Test via Telegram:
# /deepdive all    → should show scorecard
# /deepdive BTC    → should show BTC summary card
# /deepdive BTC full → should send full document in chunks
```

## STEP 7: Integrate into Morning Brief (optional enhancement)

In daily_report.py, add after the existing sections:

```python
# === DEEP DIVE ALERTS ===
try:
    from research.research_manager import load_index
    from watchlist import fetch_prices
    
    index = load_index()
    prices = fetch_prices()
    alerts = []
    
    for token, info in index.items():
        if not info.get('price') or not prices.get(token):
            continue
        current = prices[token].get('price', 0)
        research_price = info['price']
        if research_price and current:
            change = (current - research_price) / research_price * 100
            if abs(change) > 10:
                direction = "📈" if change > 0 else "📉"
                alerts.append(f"{direction} {token}: {change:+.1f}% since deep dive")
    
    if alerts:
        sections.append("📚 DEEP DIVE ALERTS\n" + "\n".join(alerts))
except Exception as e:
    log.warning("Deep dive alerts failed: %s", e)
```

## STEP 8: Commit

```bash
cd /opt/fiery-eyes
git add research/ v5_bot.py
git commit -m "deploy: /deepdive command + 12 token research library"
git push
```
