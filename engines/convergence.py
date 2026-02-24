"""Convergence Engine — cross-engine detection with strength classification.

Convergence types:
  Momentum + Adoption           = strong  (meme with real retention)
  Adoption + Infrastructure     = very strong (usage + value capture)
  All three                     = maximum conviction (extremely rare)

Triggers special Telegram DD card alert for convergence tokens.
"""

from datetime import date
from config import get_logger
from db.connection import execute
from telegram_bot.alerts import _send

log = get_logger("engines.convergence")

# Convergence threshold — engine score must be >= this
CONVERGENCE_THRESHOLD = 70

# Strength classifications
CONVERGENCE_TYPES = {
    frozenset(["momentum", "adoption"]): {
        "label": "Strong",
        "emoji": "🔥🔥",
        "description": "Meme with real adoption signals — sticky holders + growing usage",
    },
    frozenset(["adoption", "infrastructure"]): {
        "label": "Very Strong",
        "emoji": "🔥🔥🔥",
        "description": "Usage + value capture — sustainable tokenomics confirmed",
    },
    frozenset(["momentum", "infrastructure"]): {
        "label": "Strong",
        "emoji": "🔥🔥",
        "description": "Momentum with infrastructure backing — protocol with hype",
    },
    frozenset(["momentum", "adoption", "infrastructure"]): {
        "label": "Maximum Conviction",
        "emoji": "🔥🔥🔥🔥",
        "description": "Extremely rare — all engines aligned. Highest confidence signal.",
    },
}


def detect(engine_results: dict) -> dict:
    """Detect convergence from engine results.

    Args:
        engine_results: {
            "momentum": {"momentum_score": float, ...},
            "adoption": {"adoption_score": float, ...},
            "infrastructure": {"infra_score": float, ...},
        }

    Returns:
        {
            "is_converging": bool,
            "converging_engines": list[str],
            "convergence_type": str,
            "strength_label": str,
            "strength_emoji": str,
            "description": str,
            "avg_score": float,
            "min_score": float,
        }
    """
    # Map engine name to score key (infrastructure uses 'infra_score')
    score_key_map = {
        "momentum": "momentum_score",
        "adoption": "adoption_score",
        "infrastructure": "infra_score",
    }

    high_engines = {}
    for name, result in engine_results.items():
        score_key = score_key_map.get(name, f"{name}_score")
        score_val = result.get(score_key, 0)
        if score_val >= CONVERGENCE_THRESHOLD:
            high_engines[name] = score_val

    engine_set = frozenset(high_engines.keys())
    is_converging = len(high_engines) >= 2

    if not is_converging:
        return {
            "is_converging": False,
            "converging_engines": [],
            "convergence_type": "none",
            "strength_label": "None",
            "strength_emoji": "",
            "description": "",
            "avg_score": 0,
            "min_score": 0,
        }

    # Find best matching convergence type
    ctype = CONVERGENCE_TYPES.get(engine_set, {
        "label": "Strong",
        "emoji": "🔥🔥",
        "description": f"Multi-engine convergence: {', '.join(sorted(high_engines))}",
    })

    scores = list(high_engines.values())

    return {
        "is_converging": True,
        "converging_engines": sorted(high_engines.keys()),
        "convergence_type": "+".join(sorted(high_engines.keys())),
        "strength_label": ctype["label"],
        "strength_emoji": ctype["emoji"],
        "description": ctype["description"],
        "avg_score": round(sum(scores) / len(scores), 1),
        "min_score": round(min(scores), 1),
    }


def scan_all_convergences() -> list[dict]:
    """Scan all scored tokens for convergence and return results."""
    log.info("=== Scanning for convergence signals ===")

    try:
        rows = execute(
            """SELECT t.id, t.symbol, t.contract_address, t.category,
                      s.momentum_score, s.adoption_score, s.infra_score,
                      s.composite_score, s.confidence_score
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE s.date = CURRENT_DATE AND t.quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query scores for convergence: %s", e)
        return []

    if not rows:
        return []

    results = []
    for token_id, symbol, mint, category, mom, adopt, infra, comp, conf in rows:
        engine_results = {}
        if mom is not None:
            engine_results["momentum"] = {"momentum_score": mom}
        if adopt is not None:
            engine_results["adoption"] = {"adoption_score": adopt}
        if infra is not None:
            engine_results["infrastructure"] = {"infra_score": infra}

        conv = detect(engine_results)
        if conv["is_converging"]:
            conv["token_id"] = token_id
            conv["symbol"] = symbol
            conv["mint"] = mint
            conv["category"] = category
            conv["composite_score"] = comp
            conv["confidence"] = conf
            results.append(conv)

    # Sort by avg score descending
    results.sort(key=lambda r: r["avg_score"], reverse=True)

    if results:
        log.info("Found %d convergence signals", len(results))
        for r in results:
            log.info("  %s %s: %s (avg=%.0f)",
                     r["strength_emoji"], r["symbol"],
                     r["convergence_type"], r["avg_score"])
    else:
        log.info("No convergence signals found")

    return results


def send_convergence_alerts(results: list[dict]):
    """Send special Telegram alerts for convergence tokens with DD card."""
    for conv in results:
        lines = [
            f"{conv['strength_emoji']} <b>CONVERGENCE: {conv['strength_label'].upper()}</b> "
            f"{conv['strength_emoji']}",
            "",
            f"Token: <code>{conv['symbol']}</code>",
            f"Mint: <code>{conv['mint']}</code>",
            f"Category: {conv['category']}",
            "",
            f"<b>Convergence:</b> {conv['convergence_type']}",
            f"<i>{conv['description']}</i>",
            "",
            "<b>Engine Scores:</b>",
        ]

        for engine in conv["converging_engines"]:
            icon = {"momentum": "📈", "adoption": "👥", "infrastructure": "🏗"}.get(engine, "📊")
            lines.append(f"  {icon} {engine.title()}: ≥{CONVERGENCE_THRESHOLD}")

        lines.extend([
            "",
            f"<b>Composite:</b> {conv['composite_score']:.0f}/100",
            f"<b>Confidence:</b> {conv['confidence']:.0f}%",
            f"<b>Avg Converging Score:</b> {conv['avg_score']:.0f}",
            "",
            "💡 <i>Use /dd to generate full due diligence card</i>",
        ])

        _send("\n".join(lines))
