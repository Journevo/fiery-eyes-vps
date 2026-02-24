"""Composite Scorer — combines engine scores per token category,
   applies regime multiplier + virality modifier,
   calculates confidence, detects convergence, stores in scores_daily."""

from datetime import date
from config import get_logger
from db.connection import execute, execute_one
from engines import momentum, adoption, infrastructure
from engines.convergence import detect as detect_convergence
from quality_gate.helpers import get_json

log = get_logger("engines.composite")

# Engine applicability by category
CATEGORY_ENGINES = {
    "meme": ["momentum"],
    "adoption": ["momentum", "adoption"],
    "infrastructure": ["momentum", "adoption", "infrastructure"],
}


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _calculate_confidence(engine_results: dict) -> float:
    """Confidence Score based on data completeness.
    100% = all engines have sufficient data (>7 data points).
    Lower if engines are running on sparse data or stubs."""
    if not engine_results:
        return 0.0

    scores = []
    for name, result in engine_results.items():
        data_points = result.get("data_points", 0)
        if data_points >= 30:
            scores.append(100)
        elif data_points >= 14:
            scores.append(80)
        elif data_points >= 7:
            scores.append(60)
        elif data_points >= 1:
            scores.append(30)
        else:
            scores.append(10)

        # Penalize if many factors are at default 50 (indicates stub/no data)
        factors = result.get("factors", {})
        if factors:
            at_default = sum(1 for v in factors.values() if v == 50.0)
            default_pct = at_default / len(factors)
            if default_pct > 0.5:
                scores[-1] *= 0.5

    return round(sum(scores) / len(scores), 1) if scores else 0.0


# ---------------------------------------------------------------------------
# Score a single token
# ---------------------------------------------------------------------------

def score_token(token_id: int, category: str,
                mint: str | None = None,
                protocol_slug: str | None = None,
                coingecko_id: str | None = None) -> dict:
    """Run applicable engines for a token and compute composite score.

    Returns:
        {
            "token_id": int,
            "category": str,
            "engine_results": dict,
            "composite_score": float,
            "confidence": float,
            "convergence": dict,
            "regime_multiplier": float,
            "final_score": float,
            "virality": dict | None,
            "all_exit_triggers": list[str],
        }
    """
    applicable = CATEGORY_ENGINES.get(category, ["momentum"])

    engine_results = {}

    if "momentum" in applicable:
        engine_results["momentum"] = momentum.score(token_id, mint=mint)

    if "adoption" in applicable:
        engine_results["adoption"] = adoption.score(
            token_id, protocol_slug=protocol_slug, coingecko_id=coingecko_id)

    if "infrastructure" in applicable:
        engine_results["infrastructure"] = infrastructure.score(
            token_id, protocol_slug=protocol_slug, coingecko_id=coingecko_id)

    # Calculate composite score (average of applicable engines)
    score_key_map = {
        "momentum": "momentum_score",
        "adoption": "adoption_score",
        "infrastructure": "infra_score",
    }
    engine_scores = []
    momentum_val = None
    adoption_val = None
    infra_val = None

    for name, result in engine_results.items():
        score_key = score_key_map.get(name, f"{name}_score")
        val = result.get(score_key, 0)
        engine_scores.append(val)
        if name == "momentum":
            momentum_val = val
        elif name == "adoption":
            adoption_val = val
        elif name == "infrastructure":
            infra_val = val

    composite = sum(engine_scores) / len(engine_scores) if engine_scores else 0

    # Apply virality modifier to momentum-category tokens
    virality_result = None
    if mint and "momentum" in applicable:
        try:
            from virality.integrity import score as virality_score
            virality_result = virality_score(mint, token_id)
            modifier = virality_result.get("momentum_modifier", 1.0)
            # Apply modifier to momentum score and recalculate composite
            if momentum_val is not None:
                adjusted_momentum = min(100, momentum_val * modifier)
                engine_results["momentum"]["momentum_score_raw"] = momentum_val
                engine_results["momentum"]["momentum_score"] = round(adjusted_momentum, 1)
                # Recalculate composite
                engine_scores = []
                for name, result in engine_results.items():
                    score_key = score_key_map.get(name, f"{name}_score")
                    engine_scores.append(result.get(score_key, 0))
                composite = sum(engine_scores) / len(engine_scores) if engine_scores else 0
                momentum_val = adjusted_momentum
        except Exception as e:
            log.debug("Virality scoring skipped: %s", e)

    confidence = _calculate_confidence(engine_results)
    convergence = detect_convergence(engine_results)

    # Get regime multiplier
    regime_mult = _get_regime_multiplier()

    # Final score = composite × regime_multiplier
    final_score = round(composite * regime_mult, 1)

    # Collect all exit triggers
    all_triggers = []
    for result in engine_results.values():
        all_triggers.extend(result.get("exit_triggers", []))

    # Persist to scores_daily
    today = date.today()
    try:
        execute(
            """INSERT INTO scores_daily
               (token_id, date, momentum_score, adoption_score, infra_score,
                composite_score, confidence_score, regime_multiplier, final_score)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (token_id, date) DO UPDATE SET
                 momentum_score = EXCLUDED.momentum_score,
                 adoption_score = EXCLUDED.adoption_score,
                 infra_score = EXCLUDED.infra_score,
                 composite_score = EXCLUDED.composite_score,
                 confidence_score = EXCLUDED.confidence_score,
                 regime_multiplier = EXCLUDED.regime_multiplier,
                 final_score = EXCLUDED.final_score""",
            (token_id, today, momentum_val, adoption_val, infra_val,
             round(composite, 1), confidence, regime_mult, final_score),
        )
        log.info("Saved scores for token_id=%d on %s (final=%.1f)", token_id, today, final_score)
    except Exception as e:
        log.error("Failed to save scores for token_id=%d: %s", token_id, e)

    return {
        "token_id": token_id,
        "category": category,
        "engine_results": engine_results,
        "composite_score": round(composite, 1),
        "confidence": confidence,
        "convergence": convergence,
        "regime_multiplier": regime_mult,
        "final_score": final_score,
        "virality": virality_result,
        "all_exit_triggers": all_triggers,
    }


def _get_regime_multiplier() -> float:
    """Fetch today's regime multiplier from DB, default to 1.0."""
    try:
        row = execute_one(
            "SELECT regime_multiplier FROM regime_snapshots WHERE date = CURRENT_DATE",
        )
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return 1.0


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------

def _resolve_missing_names(rows: list) -> None:
    """Backfill token name/symbol from DexScreener for rows missing names.
    Mutates the row tuples in-place (converts to lists)."""
    for i, row in enumerate(rows):
        token_id, mint, symbol, name, category = row
        if name or category == "infrastructure":
            continue
        # Looks like a Solana mint address (long base58)
        if len(mint) < 30:
            continue
        try:
            data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            pairs = data.get("pairs") or []
            if pairs:
                base = pairs[0].get("baseToken", {})
                new_name = base.get("name", "")
                new_symbol = base.get("symbol", "")
                if new_name:
                    execute(
                        "UPDATE tokens SET name = %s, symbol = %s WHERE id = %s",
                        (new_name, new_symbol, token_id),
                    )
                    rows[i] = (token_id, mint, new_symbol, new_name, category)
                    log.info("Resolved name for token_id=%d: %s ($%s)", token_id, new_name, new_symbol)
        except Exception as e:
            log.debug("DexScreener name lookup failed for %s: %s", mint[:12], e)


def score_all_tokens() -> list[dict]:
    """Score all gate-pass tokens and return results sorted by final score."""
    log.info("=== Scoring all gate-pass tokens ===")

    try:
        rows = execute(
            """SELECT id, contract_address, symbol, name, category
               FROM tokens WHERE quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query tokens for scoring: %s", e)
        return []

    if not rows:
        log.info("No gate-pass tokens to score")
        return []

    # Convert to mutable lists and resolve missing names
    rows = [list(r) for r in rows]
    _resolve_missing_names(rows)

    results = []
    for token_id, mint, symbol, name, category in rows:
        cat = category or "meme"

        # For infra tokens, contract_address is the coingecko_id
        coingecko_id = mint if cat == "infrastructure" else None
        # Derive DeFiLlama slug from token name for adoption/infra
        protocol_slug = (name or symbol or "").lower().replace(" ", "-") if cat in ("adoption", "infrastructure") else None

        try:
            result = score_token(token_id, cat, mint=mint,
                                 protocol_slug=protocol_slug,
                                 coingecko_id=coingecko_id)
            result["mint"] = mint
            result["symbol"] = symbol
            result["name"] = name or ""
            results.append(result)
        except Exception as e:
            log.error("Failed to score token_id=%d (%s): %s", token_id, mint, e)

    # Sort by final score descending
    results.sort(key=lambda r: r.get("final_score", 0), reverse=True)

    log.info("Scored %d tokens. Top 3: %s",
             len(results),
             ", ".join(f"{r.get('name') or r['symbol']}={r.get('final_score', 0)}" for r in results[:3]))

    return results
