"""Service for generating CISO and CTO/VP Eng reports from scan data.

CISO report: Focused on speed, correctness, and coverage — proves that
             automated remediation actually works and closes the backlog fast.

CTO report:  Focused on engineering efficiency, ROI, and workflow integration —
             proves the investment pays for itself and doesn't burden the team.
"""

import logging
from datetime import datetime, timezone

from app.models.schemas import BranchSummary

logger = logging.getLogger(__name__)

TOOL_DISPLAY_NAMES = {
    "devin": "Devin",
    "copilot": "Copilot",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Google",
}

# Cost per million tokens (USD)
# Anthropic claude-opus-4-6
_ANTHROPIC_INPUT_COST_PER_MTOK = 5.0
_ANTHROPIC_OUTPUT_COST_PER_MTOK = 25.0
# OpenAI gpt-5.3-codex
_OPENAI_INPUT_COST_PER_MTOK = 1.75
_OPENAI_OUTPUT_COST_PER_MTOK = 14.0
# Google gemini-3.1-pro-preview (prompts <= 200k tokens)
_GEMINI_INPUT_COST_PER_MTOK = 2.0
_GEMINI_OUTPUT_COST_PER_MTOK = 12.0

# Devin: $2.00 per ACU
_DEVIN_COST_PER_ACU = 2.0

# Copilot Autofix: $0.04 per request (flat rate per alert)
_COPILOT_COST_PER_REQUEST = 0.04

# Rough estimate: ~4500 input tokens per alert
# Breakdown: ~3000-4000 tokens for full source file + ~500 for alert context
# (CWE description, rule ID, affected lines) + ~200 for prompt instructions
_ESTIMATED_INPUT_TOKENS_PER_ALERT = 4500


def _estimate_tool_cost(
    tool_name: str,
    alerts_processed: int,
    estimated_input_tokens: int | None = None,
    devin_acus: float | None = None,
) -> dict | None:
    """Estimate the cost for any tool.

    For API-based tools (anthropic, openai, gemini): token-based pricing.
    For copilot: flat $0.04 per request/alert.
    For devin: $2.00 per ACU.
    """
    if tool_name in ("anthropic", "openai", "gemini"):
        return _estimate_api_cost(tool_name, alerts_processed, estimated_input_tokens)
    elif tool_name == "copilot":
        return _estimate_copilot_cost(alerts_processed)
    elif tool_name == "devin":
        return _estimate_devin_cost(alerts_processed, devin_acus)
    return None


def _estimate_api_cost(tool_name: str, alerts_processed: int, estimated_input_tokens: int | None = None) -> dict | None:
    """Estimate the API cost for a token-based tool."""
    if tool_name == "anthropic":
        input_cost_per_mtok = _ANTHROPIC_INPUT_COST_PER_MTOK
        output_cost_per_mtok = _ANTHROPIC_OUTPUT_COST_PER_MTOK
        model = "claude-opus-4-6"
    elif tool_name == "openai":
        input_cost_per_mtok = _OPENAI_INPUT_COST_PER_MTOK
        output_cost_per_mtok = _OPENAI_OUTPUT_COST_PER_MTOK
        model = "gpt-5.3-codex"
    elif tool_name == "gemini":
        input_cost_per_mtok = _GEMINI_INPUT_COST_PER_MTOK
        output_cost_per_mtok = _GEMINI_OUTPUT_COST_PER_MTOK
        model = "gemini-3.1-pro-preview"
    else:
        return None

    if estimated_input_tokens and estimated_input_tokens > 0:
        input_tokens = estimated_input_tokens
    else:
        input_tokens = alerts_processed * _ESTIMATED_INPUT_TOKENS_PER_ALERT

    output_tokens = input_tokens  # approximate: output ≈ input

    input_cost = (input_tokens / 1_000_000) * input_cost_per_mtok
    output_cost = (output_tokens / 1_000_000) * output_cost_per_mtok
    total_cost = input_cost + output_cost

    return {
        "model": model,
        "pricing_type": "token",
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "input_cost_usd": round(input_cost, 4),
        "output_cost_usd": round(output_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "pricing": {
            "input_per_mtok_usd": input_cost_per_mtok,
            "output_per_mtok_usd": output_cost_per_mtok,
        },
    }


def _estimate_copilot_cost(alerts_processed: int) -> dict:
    """Estimate the cost for Copilot Autofix — flat $0.04 per request."""
    total_cost = alerts_processed * _COPILOT_COST_PER_REQUEST
    return {
        "model": "Copilot Autofix",
        "pricing_type": "per_request",
        "alerts_processed": alerts_processed,
        "cost_per_request_usd": _COPILOT_COST_PER_REQUEST,
        "total_cost_usd": round(total_cost, 4),
    }


def _estimate_devin_cost(alerts_processed: int, acus: float | None = None) -> dict:
    """Estimate the cost for Devin — $2.00 per ACU."""
    if acus and acus > 0:
        total_cost = acus * _DEVIN_COST_PER_ACU
    else:
        # Estimate: ~0.5 ACU per alert as a rough average
        acus = alerts_processed * 0.5
        total_cost = acus * _DEVIN_COST_PER_ACU

    return {
        "model": "Devin (ACU-based)",
        "pricing_type": "acu",
        "acus": round(acus, 2),
        "cost_per_acu_usd": _DEVIN_COST_PER_ACU,
        "alerts_processed": alerts_processed,
        "total_cost_usd": round(total_cost, 4),
    }



def generate_ciso_report(
    repo: str,
    scan_created_at: str,
    baseline_summary: BranchSummary,
    tool_summaries: dict[str, BranchSummary],
    baseline_alerts: list[dict],
    tool_alerts_map: dict[str, list[dict]],
    remediation_times: dict[str, float] | None = None,
) -> dict:
    """Generate a CISO-focused report: speed, correctness, coverage.

    A CISO already knows CodeQL findings matter.  They want to know:
    1. How fast will these get fixed?  (MTTR)
    2. Will the fixes actually work?  (CodeQL re-scan verification, regressions)
    3. How much of the backlog goes away?  (coverage by severity)
    4. Do engineers have to babysit it?  (automation level)
    """
    now = datetime.now(timezone.utc).isoformat()

    # --- Per-tool performance ---
    tool_performance: dict[str, dict] = {}
    best_tool = None
    best_fix_rate = 0.0

    for tool_name, summary in tool_summaries.items():
        total_fixed = max(baseline_summary.open - summary.open, 0)
        fix_rate = round((total_fixed / baseline_summary.open * 100), 1) if baseline_summary.open > 0 else 0.0
        critical_fixed = max(baseline_summary.critical - summary.critical, 0)
        high_fixed = max(baseline_summary.high - summary.high, 0)

        # Regressions: alerts on tool branch that don't exist on baseline
        tool_alert_numbers = {a["number"] for a in tool_alerts_map.get(tool_name, [])}
        baseline_numbers = {a["number"] for a in baseline_alerts}
        regressions = len(tool_alert_numbers - baseline_numbers)

        # Automation level
        automation = "fully automated"
        if tool_name == "copilot":
            automation = "requires manual acceptance per suggestion"
        elif tool_name == "anthropic":
            automation = "requires patch review and application (claude-opus-4-6)"
        elif tool_name == "openai":
            automation = "requires patch review and application (gpt-5.3-codex)"
        elif tool_name == "gemini":
            automation = "requires patch review and application (gemini-3.1-pro-preview)"

        perf: dict = {
            "total_fixed": total_fixed,
            "fix_rate_pct": fix_rate,
            "critical_fixed": critical_fixed,
            "high_fixed": high_fixed,
            "remaining_open": summary.open,
            "remaining_critical": summary.critical,
            "remaining_high": summary.high,
            "regressions_introduced": regressions,
            "automation_level": automation,
        }

        # Speed / MTTR
        if remediation_times and tool_name in remediation_times:
            secs = remediation_times[tool_name]
            avg_secs = secs / total_fixed if total_fixed > 0 else 0
            perf["total_time"] = _format_duration(secs)
            perf["avg_time_per_fix"] = _format_duration(avg_secs)
            perf["total_seconds"] = round(secs, 1)
            perf["avg_seconds_per_fix"] = round(avg_secs, 1)

        # Cost estimate for all tools
        cost = _estimate_tool_cost(
            tool_name, baseline_summary.open, baseline_summary.estimated_prompt_tokens,
        )
        if cost:
            perf["cost_estimate"] = cost

        tool_performance[tool_name] = perf

        if fix_rate > best_fix_rate:
            best_fix_rate = fix_rate
            best_tool = tool_name

    # --- Severity breakdown before/after ---
    severity_before_after: dict[str, dict] = {}
    for tool_name, summary in tool_summaries.items():
        severity_before_after[tool_name] = {
            "critical": {
                "before": baseline_summary.critical,
                "after": summary.critical,
                "fixed": max(baseline_summary.critical - summary.critical, 0),
            },
            "high": {
                "before": baseline_summary.high,
                "after": summary.high,
                "fixed": max(baseline_summary.high - summary.high, 0),
            },
            "medium": {
                "before": baseline_summary.medium,
                "after": summary.medium,
                "fixed": max(baseline_summary.medium - summary.medium, 0),
            },
            "low": {
                "before": baseline_summary.low,
                "after": summary.low,
                "fixed": max(baseline_summary.low - summary.low, 0),
            },
        }

    # --- Headline number ---
    best_perf = tool_performance.get(best_tool, {}) if best_tool else {}

    return {
        "report_type": "ciso",
        "title": "Security Remediation Report",
        "generated_at": now,
        "repo": repo,
        "scan_date": scan_created_at,

        "headline": {
            "baseline_open": baseline_summary.open,
            "baseline_critical": baseline_summary.critical,
            "baseline_high": baseline_summary.high,
            "best_tool": best_tool,
            "best_fix_rate_pct": best_fix_rate,
            "best_total_fixed": best_perf.get("total_fixed", 0),
            "best_time": best_perf.get("total_time"),
            "best_regressions": best_perf.get("regressions_introduced", 0),
        },

        "tool_performance": tool_performance,

        "severity_before_after": severity_before_after,

        "verification": {
            "method": "CodeQL re-scan on each tool branch after fixes are pushed",
            "description": (
                "Every fix is verified by running the same CodeQL analysis that "
                "originally detected the vulnerability. Only alerts that CodeQL "
                "marks as 'fixed' are counted — no false claims."
            ),
        },
    }


def generate_cto_report(
    repo: str,
    scan_created_at: str,
    baseline_summary: BranchSummary,
    tool_summaries: dict[str, BranchSummary],
    baseline_alerts: list[dict],
    tool_alerts_map: dict[str, list[dict]],
    remediation_times: dict[str, float] | None = None,
    avg_engineer_hourly_cost: float = 75.0,
    avg_manual_fix_minutes: float = 30.0,
) -> dict:
    """Generate a CTO/VP Eng focused efficiency and ROI report.

    A CTO/VP Eng wants to know:
    1. Which tool fixes the most with the least effort?
    2. How much engineering time does this save?
    3. What's the ROI vs manual remediation?
    4. How does it fit into our workflow?
    """
    now = datetime.now(timezone.utc).isoformat()

    # --- Tool comparison ---
    tool_comparison: dict[str, dict] = {}
    best_tool = None
    best_fix_rate = 0.0

    for tool_name, summary in tool_summaries.items():
        total_fixed = max(baseline_summary.open - summary.open, 0)
        fix_rate = round((total_fixed / baseline_summary.open * 100), 1) if baseline_summary.open > 0 else 0.0

        # Regressions
        tool_alert_numbers = {a["number"] for a in tool_alerts_map.get(tool_name, [])}
        baseline_numbers = {a["number"] for a in baseline_alerts}
        new_alerts = len(tool_alert_numbers - baseline_numbers)

        human_intervention = "none (fully automated)"
        if tool_name == "copilot":
            human_intervention = "manual acceptance of each suggestion"
        elif tool_name == "anthropic":
            human_intervention = "patch review and application (claude-opus-4-6)"
        elif tool_name == "openai":
            human_intervention = "patch review and application (gpt-5.3-codex)"
        elif tool_name == "gemini":
            human_intervention = "patch review and application (gemini-3.1-pro-preview)"

        entry: dict = {
            "total_fixed": total_fixed,
            "fix_rate_pct": fix_rate,
            "remaining_open": summary.open,
            "new_alerts_introduced": new_alerts,
            "human_intervention": human_intervention,
        }

        if remediation_times and tool_name in remediation_times:
            secs = remediation_times[tool_name]
            entry["total_time"] = _format_duration(secs)
            entry["avg_time_per_fix"] = _format_duration(secs / total_fixed) if total_fixed > 0 else "N/A"

        # Cost estimate for all tools
        cost = _estimate_tool_cost(
            tool_name, baseline_summary.open, baseline_summary.estimated_prompt_tokens,
        )
        if cost:
            entry["cost_estimate"] = cost

        tool_comparison[tool_name] = entry

        if fix_rate > best_fix_rate:
            best_fix_rate = fix_rate
            best_tool = tool_name

    # --- ROI ---
    roi: dict[str, dict] = {}
    for tool_name, comp in tool_comparison.items():
        fixed = comp["total_fixed"]
        manual_hours = fixed * avg_manual_fix_minutes / 60
        manual_cost = round(manual_hours * avg_engineer_hourly_cost, 2)

        tool_cost = comp.get("cost_estimate", {}).get("total_cost_usd", 0)
        savings = round(manual_cost - tool_cost, 2)

        roi[tool_name] = {
            "alerts_fixed": fixed,
            "developer_hours_saved": round(manual_hours, 1),
            "manual_cost_usd": manual_cost,
            "tool_cost_usd": round(tool_cost, 4),
            "net_savings_usd": savings,
            "roi_pct": round((savings / manual_cost * 100), 1) if manual_cost > 0 else 0.0,
        }

    # --- Backlog impact ---
    backlog: dict[str, dict] = {}
    for tool_name, summary in tool_summaries.items():
        reduction_pct = round((1 - summary.open / baseline_summary.open) * 100, 1) if baseline_summary.open > 0 else 0.0
        backlog[tool_name] = {
            "before": baseline_summary.open,
            "after": summary.open,
            "reduction_pct": reduction_pct,
        }

    return {
        "report_type": "cto",
        "title": "Engineering Efficiency & ROI Report",
        "generated_at": now,
        "repo": repo,
        "scan_date": scan_created_at,
        "executive_summary": {
            "baseline_open_alerts": baseline_summary.open,
            "best_tool": best_tool,
            "best_fix_rate_pct": best_fix_rate,
            "total_tools_compared": len(tool_summaries),
        },
        "tool_comparison": tool_comparison,
        "roi_analysis": {
            "assumptions": {
                "avg_engineer_hourly_cost_usd": avg_engineer_hourly_cost,
                "avg_manual_fix_minutes": avg_manual_fix_minutes,
            },
            "tools": roi,
        },
        "price_comparison": _build_price_comparison(tool_comparison),
        "backlog_impact": backlog,
        "integration_workflow": {
            "description": "Automated remediation pipeline — zero engineer time required",
            "steps": [
                "CodeQL scan detects vulnerabilities on push",
                "Platform fetches new alerts via GitHub API",
                "Devin API creates automated fix sessions per alert",
                "Fixes are tested in Devin's sandboxed environment",
                "Fixes are pushed to tool-specific branches",
                "CodeQL re-scans to verify fixes — no false claims",
            ],
        },
        "recommendation": _generate_recommendation(tool_comparison, best_tool),
    }


def _build_price_comparison(tool_data: dict) -> dict:
    """Build a side-by-side price comparison across all tools."""
    comparison: dict[str, dict] = {}
    for tool_name, data in tool_data.items():
        cost_est = data.get("cost_estimate")
        if not cost_est:
            continue
        fixed = data.get("total_fixed", 0)
        total = cost_est.get("total_cost_usd", 0)
        cost_per_fix = round(total / fixed, 4) if fixed > 0 else float('inf')
        comparison[tool_name] = {
            "display_name": TOOL_DISPLAY_NAMES.get(tool_name, tool_name),
            "pricing_type": cost_est.get("pricing_type", "unknown"),
            "model": cost_est.get("model", ""),
            "total_cost_usd": total,
            "alerts_fixed": fixed,
            "cost_per_fix_usd": cost_per_fix,
        }

    # Rank by cost per fix (cheapest first)
    ranked = sorted(comparison.items(), key=lambda x: x[1]["cost_per_fix_usd"])
    cheapest = ranked[0][0] if ranked else None
    most_expensive = ranked[-1][0] if ranked else None

    return {
        "tools": comparison,
        "cheapest_tool": cheapest,
        "most_expensive_tool": most_expensive,
        "ranked_by_cost_per_fix": [t for t, _ in ranked],
    }


def _generate_recommendation(tool_matrix: dict, best_tool: str | None) -> dict:
    """Generate a recommendation based on the comparison results."""
    if not best_tool:
        return {
            "tool": None,
            "summary": "Insufficient data to make a recommendation.",
            "details": "Run a scan with multiple tools to generate a comparison.",
        }

    best = tool_matrix[best_tool]
    display_name = TOOL_DISPLAY_NAMES.get(best_tool, best_tool)

    return {
        "tool": best_tool,
        "summary": (
            f"{display_name} achieved the highest fix rate at {best['fix_rate_pct']}% "
            f"with {best['human_intervention']} human intervention required."
        ),
        "details": (
            f"{display_name} fixed {best['total_fixed']} alerts and introduced "
            f"{best['new_alerts_introduced']} new alerts. "
            f"Based on fix rate, automation level, and code quality, "
            f"{display_name} is recommended for automated CodeQL remediation."
        ),
    }


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        mins = seconds / 60
        return f"{mins:.1f}min"
    hours = seconds / 3600
    return f"{hours:.1f}hr"
