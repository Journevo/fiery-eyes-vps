#!/usr/bin/env python3
"""Fiery Eyes — Solana Memecoin Intelligence System — Entry Point.

Commands:
  python main.py <token_address> [category]   Scan single token (full pipeline)
  python main.py scan                         Start auto-scanner with all modules
  python main.py server                       Health endpoint only
  python main.py snapshots                    Run daily snapshot collection
  python main.py scores                       Run engine scoring
  python main.py clusters                     Run cluster detection
  python main.py regime                       Calculate regime multiplier
  python main.py report                       Generate nightly strategist report
  python main.py performance                  Update performance tracking + weekly report
  python main.py exits                        Check all exit triggers
  python main.py dd <token_address>           Generate DD card
  python main.py bot                          Start Telegram bot command polling
  python main.py convergence                  Scan for convergence signals
  python main.py wallets                      Wallet tracker operations
  python main.py lifecycle                    Run lifecycle stage detection
  python main.py adoption-scan               Run adoption track discovery
  python main.py infra-scan                  Run infrastructure track discovery
  python main.py watchlist [track]            Show watchlist
  python main.py social <keyword> [mint]      Social pulse check
  python main.py oi <symbol>                  OI + funding analysis
  python main.py unlocks <symbol>             Token unlock schedule
  python main.py recheck-watch               Re-check watching tokens
  python main.py health <token_address>      Run Health Score v2 on a token
  python main.py kol-check                   One-time check of all KOL wallets
  python main.py shadow-update               Update all open shadow trades
  python main.py shadow-report               Print shadow trading summary
  python main.py seed-kols                   Seed KOL wallet database
  python main.py huoyan                      Generate and send Huoyan pulse
  python main.py grok-poll                   One-time smart money X poll
"""

import sys
import threading
from config import get_logger
from quality_gate.gate import run_gate
from telegram_bot.alerts import send_gate_result, send_scored_alert, send_message
from health import run_health_server, set_last_gate_run

log = get_logger("main")


def scan_token(mint: str, category: str = "meme"):
    """Run the Quality Gate on a single token, collect snapshot, score, and alert."""
    log.info("Scanning token: %s", mint)
    result = run_gate(mint, category=category)
    set_last_gate_run(result["timestamp"])

    gate_status = result.get("gate_status", "rejected")

    if result["overall_pass"] or gate_status == "watching":
        try:
            from collectors.snapshots import collect_snapshot
            from db.connection import execute_one
            row = execute_one(
                "SELECT id FROM tokens WHERE contract_address = %s", (mint,))
            if row:
                collect_snapshot(mint, row[0])

                if result["overall_pass"]:
                    from engines.composite import score_token
                    score_result = score_token(row[0], category, mint=mint)
                    send_scored_alert(result, score_result)

                    print(f"\n{'='*60}")
                    print(f"  Quality Gate: ✅ FULL PASS")
                    print(f"  Token: {mint}")
                    print(f"  Composite: {score_result['composite_score']:.0f}/100")
                    print(f"  Regime: {score_result['regime_multiplier']:.3f}")
                    print(f"  Final Score: {score_result['final_score']:.0f}/100")
                    print(f"  Confidence: {score_result['confidence']:.0f}%")
                    if score_result["convergence"]["is_converging"]:
                        engines = score_result["convergence"]["converging_engines"]
                        print(f"  Convergence: {', '.join(e.title() for e in engines)}")
                    if score_result.get("virality"):
                        v = score_result["virality"]
                        print(f"  Virality: {v['adjusted_virality']:.0f} (integrity {v['integrity']:.0f})")
                    if score_result["all_exit_triggers"]:
                        print(f"  Triggers: {', '.join(score_result['all_exit_triggers'])}")
                    print(f"{'='*60}\n")
                    return result
                else:
                    # Watching — lighter scoring with confidence penalty
                    from engines.composite import score_token
                    score_result = score_token(row[0], "meme", mint=mint)
                    velocity = result.get("velocity_signals", [])
                    print(f"\n{'='*60}")
                    print(f"  Quality Gate: 👀 WATCHING")
                    print(f"  Token: {mint}")
                    print(f"  Velocity: {', '.join(velocity)}")
                    print(f"  Score: {score_result['composite_score']:.0f}/100 (0.7x confidence)")
                    print(f"{'='*60}\n")
                    return result
        except Exception as e:
            log.error("Post-gate scoring failed: %s", e)
            send_gate_result(result)
    else:
        send_gate_result(result)

    status_map = {
        "passed": "✅ FULL PASS",
        "watching": "👀 WATCHING",
        "rejected": "❌ REJECT",
    }
    status = status_map.get(gate_status, "❌ REJECT")
    print(f"\n{'='*60}")
    print(f"  Quality Gate: {status}")
    print(f"  Token: {mint}")
    print(f"  Passed: {7 - len(result['failures'])}/7 checks")
    if result["failures"]:
        print(f"  Failed: {', '.join(result['failures'])}")
    velocity = result.get("velocity_signals", [])
    if velocity:
        print(f"  Velocity: {', '.join(velocity)}")
    print(f"{'='*60}\n")

    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "server":
        log.info("Starting Fiery Eyes in server mode")
        send_message("🔥 <b>Fiery Eyes</b> is online")
        run_health_server()

    elif cmd == "scan":
        from scanner.scheduler import run_scanner
        log.info("Starting Fiery Eyes in auto-scan mode")
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        run_scanner()

    elif cmd == "scan-once":
        log.info("Running single scan cycle")
        from scanner.discover import discover_new_tokens
        new_mints = discover_new_tokens()
        if not new_mints:
            print("No new tokens found.")
        else:
            passed = watching = failed = 0
            for mint in new_mints:
                try:
                    result = run_gate(mint, category="meme")
                    set_last_gate_run(result["timestamp"])
                    gs = result.get("gate_status", "rejected")
                    if gs == "passed":
                        passed += 1
                        send_gate_result(result)
                    elif gs == "watching":
                        watching += 1
                        send_gate_result(result)
                    else:
                        failed += 1
                except Exception as e:
                    log.error("Error scanning %s: %s", mint, e)
                    failed += 1
            print(f"Scanned {len(new_mints)}: {passed} passed, {watching} watching, {failed} rejected")

    elif cmd == "snapshots":
        log.info("Running manual snapshot collection")
        from collectors.snapshots import collect_all_snapshots
        collect_all_snapshots()

    elif cmd == "scores":
        log.info("Running manual engine scoring")
        from engines.composite import score_all_tokens
        from telegram_bot.alerts import send_daily_summary
        results = score_all_tokens()
        send_daily_summary(results)

    elif cmd == "clusters":
        log.info("Running manual cluster detection")
        from collectors.cluster import run_cluster_detection_all
        run_cluster_detection_all()

    elif cmd == "regime":
        log.info("Calculating regime multiplier")
        from regime.multiplier import calculate_regime
        result = calculate_regime()
        print(f"\nRegime Multiplier: {result['regime_multiplier']:.3f}")
        print(f"Guidance: {result['allocation_guidance']}")
        for k, v in result['components'].items():
            print(f"  {k}: {v:.3f}")
        if result.get("raw_data", {}).get("btc_price"):
            print(f"\nBTC: ${result['raw_data']['btc_price']:,.0f}")
            print(f"Fear & Greed: {result['raw_data'].get('fear_greed_value', 'N/A')} "
                  f"({result['raw_data'].get('fear_greed_classification', 'N/A')})")

    elif cmd == "report":
        log.info("Generating nightly strategist report")
        from reports.nightly import generate_nightly_report
        generate_nightly_report()

    elif cmd == "performance":
        log.info("Running performance tracking")
        from reports.performance import update_performance_prices, generate_weekly_report
        update_performance_prices()
        generate_weekly_report()

    elif cmd == "exits":
        log.info("Checking all exit triggers")
        from risk.exits import check_all_exits
        from regime.multiplier import get_current_regime
        regime = get_current_regime()
        mult = regime["regime_multiplier"] if regime else 1.0
        results = check_all_exits(mult)
        if results:
            print(f"\nExit triggers found for {len(results)} tokens:")
            for r in results:
                print(f"  {r.get('symbol', '?')}: {len(r['triggers'])} triggers")
                for t in r["triggers"]:
                    print(f"    [{t['severity']}] {t['trigger']}: {t['detail']}")
        else:
            print("\nNo exit triggers active.")

    elif cmd == "dd":
        if len(sys.argv) < 3:
            print("Usage: python main.py dd <token_address>")
            sys.exit(1)
        mint = sys.argv[2]
        log.info("Generating DD card for %s", mint)
        from reports.dd_card import generate_dd_card
        generate_dd_card(mint)

    elif cmd == "bot":
        log.info("Starting Telegram bot polling")
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        from telegram_bot.commands import start_bot_polling
        start_bot_polling()

    elif cmd == "convergence":
        log.info("Scanning for convergence signals")
        from engines.convergence import scan_all_convergences, send_convergence_alerts
        results = scan_all_convergences()
        if results:
            send_convergence_alerts(results)
            print(f"\nFound {len(results)} convergence signal(s):")
            for r in results:
                print(f"  {r['strength_emoji']} {r['symbol']}: "
                      f"{r['convergence_type']} (avg={r['avg_score']:.0f})")
        else:
            print("\nNo convergence signals found.")

    elif cmd == "wallets":
        log.info("Wallet tracker operations")
        from wallets.tracker import initialize_wallets, get_tracked_wallets_summary
        if len(sys.argv) > 2 and sys.argv[2] == "init":
            initialize_wallets()
        summary = get_tracked_wallets_summary()
        print(f"\nTracked wallets: {summary['total']} (active: {summary['active']})")
        print(f"Avg reputation: {summary['avg_reputation']:.1f}")

    elif cmd == "lifecycle":
        log.info("Running lifecycle stage detection")
        from engines.lifecycle import check_promotion_candidates, get_lifecycle_summary
        summary = get_lifecycle_summary()
        stage_names = {1: "Birth", 2: "Viral", 3: "Community", 4: "Adoption", 5: "Infrastructure"}
        stage_counts = summary.get("stage_counts", {})
        print("\nLifecycle Summary:")
        for s in range(1, 6):
            count = stage_counts.get(s, 0)
            print(f"  Stage {s} ({stage_names[s]}): {count} tokens")
        candidates = check_promotion_candidates()
        if candidates:
            print(f"\nPromotion Candidates ({len(candidates)}):")
            for c in candidates:
                print(f"  🎓 {c.get('symbol', '?')}: ready for Stage {c.get('new_stage', '?')}")

    elif cmd == "adoption-scan":
        log.info("Running adoption track discovery")
        from scanner.adoption_discover import run_adoption_discovery
        run_adoption_discovery()

    elif cmd == "infra-scan":
        log.info("Running infrastructure track discovery")
        from scanner.infra_discover import run_infrastructure_discovery
        run_infrastructure_discovery()

    elif cmd == "watchlist":
        track = sys.argv[2] if len(sys.argv) > 2 else None
        from scanner.watchlists.manager import handle_watchlist_command
        print(handle_watchlist_command(track))

    elif cmd == "social":
        if len(sys.argv) < 3:
            print("Usage: python main.py social <keyword> [mint]")
            sys.exit(1)
        keyword = sys.argv[2]
        mint = sys.argv[3] if len(sys.argv) > 3 else None
        log.info("Running social pulse for %s", keyword)
        from social.pulse import calculate_pulse
        result = calculate_pulse(keyword, mint=mint)
        print(f"\nSocial Pulse: {result['pulse_score']:.0f}/100")
        print(f"Cross-platform: {result['cross_platform_count']} platforms")
        print(f"High conviction: {'YES' if result['high_conviction'] else 'no'}")
        for platform, score in result.get("platform_scores", {}).items():
            print(f"  {platform}: {score:.0f}/100")

    elif cmd == "oi":
        if len(sys.argv) < 3:
            print("Usage: python main.py oi <symbol>")
            sys.exit(1)
        symbol = sys.argv[2]
        log.info("Running OI analysis for %s", symbol)
        from market_intel.oi_analyzer import get_market_structure_summary
        result = get_market_structure_summary(symbol)
        if result:
            print(f"\nOI Analysis: {symbol}")
            print(f"  OI Regime: {result.get('oi_regime', 'N/A')}")
            print(f"  Funding Signal: {result.get('funding_signal', 'N/A')}")
            print(f"  Leverage Risk: {result.get('leverage_risk', 0):.0f}/100")
            print(f"  Interpretation: {result.get('interpretation', 'N/A')}")
        else:
            print(f"\nNo OI data available for {symbol}")

    elif cmd == "unlocks":
        if len(sys.argv) < 3:
            print("Usage: python main.py unlocks <symbol>")
            sys.exit(1)
        symbol = sys.argv[2]
        log.info("Checking unlocks for %s", symbol)
        from market_intel.unlocks import get_upcoming_unlocks, calculate_unlock_risk
        unlocks = get_upcoming_unlocks(symbol)
        if unlocks:
            print(f"\nUpcoming unlocks for {symbol}:")
            for u in unlocks:
                print(f"  {u.get('date', '?')}: {u.get('pct_of_supply', 0):.1f}% ({u.get('type', 'linear')})")
        else:
            print(f"\nNo upcoming unlocks for {symbol}")
        risk = calculate_unlock_risk(symbol, 0)
        if risk:
            print(f"  Risk: {risk.get('risk_level', 'N/A')} (ratio: {risk.get('unlock_to_volume_ratio', 0):.1f}x)")

    elif cmd == "youtube-scan":
        log.info("Running YouTube channel scan")
        from social.youtube_free import run_youtube_scan
        results = run_youtube_scan()
        print(f"YouTube scan complete: {len(results)} new videos processed")

    elif cmd == "youtube-check":
        # Alias for youtube-scan
        log.info("Running YouTube channel check")
        from social.youtube_free import run_youtube_scan
        results = run_youtube_scan()
        print(f"YouTube check complete: {len(results)} new videos processed")

    elif cmd == "youtube-digest":
        log.info("Running YouTube daily digest")
        from social.youtube_free import run_daily_digest
        run_daily_digest()

    elif cmd == "recheck-watch":
        log.info("Re-checking watching tokens")
        from quality_gate.gate import recheck_watching_tokens
        results = recheck_watching_tokens()
        print(f"\nRe-checked watching tokens: {len(results)} processed")
        for r in results:
            print(f"  {r.get('mint', '?')[:12]}...: {r.get('gate_status', '?')}")

    elif cmd == "health":
        if len(sys.argv) < 3:
            print("Usage: python main.py health <token_address>")
            sys.exit(1)
        token_addr = sys.argv[2]
        log.info("Running health score for %s", token_addr)
        from health_score.engine import score_token
        result = score_token(token_addr)
        print(f"\n{'='*60}")
        print(f"  Health Score: {result['scaled_score']:.1f}/100")
        print(f"  Confidence: {result['confidence_pct']:.0f}%")
        print(f"  Action: {result['recommended_action']}")
        print(f"  Auto-enabled: {result['auto_action_enabled']}")
        print(f"  Tier: {result['token_tier']}")
        print(f"  Regime: {result['regime_state']}")
        print(f"  Scores: Vol={result['volume_score']:.1f}/30  "
              f"Price={result['price_score']:.1f}/20  "
              f"KOL={result['kol_score']:.1f}/20")
        print(f"  Data: Vol={result['volume_data_state']}  "
              f"Price={result['price_data_state']}  "
              f"KOL={result['kol_data_state']}")
        liq = result.get('_details', {}).get('liquidity', {})
        if liq:
            print(f"  Liquidity: ratio={liq.get('ratio', 0):.1f}x  "
                  f"restriction={liq.get('restriction', '?')}  "
                  f"LP={liq.get('lp_direction', '?')}")
        print(f"{'='*60}\n")

    elif cmd == "kol-check":
        log.info("Checking all KOL wallets")
        from kol_tracking.monitor import check_kol_wallets
        check_kol_wallets()
        print("KOL wallet check complete")

    elif cmd == "shadow-update":
        log.info("Updating shadow trades")
        from shadow.tracker import update_shadow_trades
        update_shadow_trades()
        print("Shadow trades updated")

    elif cmd == "shadow-report":
        from shadow.tracker import get_shadow_report
        print(get_shadow_report())

    elif cmd == "seed-kols":
        log.info("Seeding KOL wallets")
        from kol_tracking.seed_wallets import seed_kol_wallets
        seed_kol_wallets()

    elif cmd == "huoyan":
        log.info("Generating Huoyan pulse")
        from telegram_bot.huoyan_pulse import generate_pulse
        report = generate_pulse()
        print(report)

    elif cmd == "grok-poll":
        log.info("Running one-time smart money X poll")
        from social.grok_poller import run_smart_money_poll
        result = run_smart_money_poll()
        print(f"\nSmart Money Poll Results:")
        print(f"  Total new signals: {result.get('total_signals', 0)}")
        for handle, count in result.get("per_account", {}).items():
            print(f"  @{handle}: {count} signals")
        if result.get("errors"):
            print(f"  Errors: {', '.join(result['errors'])}")

    else:
        # Assume it's a token address
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        mint = cmd
        category = sys.argv[2] if len(sys.argv) > 2 else "meme"
        scan_token(mint, category)


if __name__ == "__main__":
    main()
