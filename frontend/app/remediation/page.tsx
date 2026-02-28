"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import Link from "next/link";
import {
  api,
  type Alert,
  type CostEstimate,
  type ReplayRunWithEvents,
  type ReplayEvent,
} from "@/lib/api";
import { useRepo } from "@/lib/repo-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

// ---------------------------------------------------------------------------
// Constants (mirrored from replay page for consistency)
// ---------------------------------------------------------------------------

const TOOL_COLORS: Record<string, string> = {
  devin: "bg-emerald-500",
  copilot: "bg-blue-500",
  anthropic: "bg-orange-500",
  openai: "bg-violet-500",
  gemini: "bg-pink-500",
};

const TOOL_TEXT_COLORS: Record<string, string> = {
  devin: "text-emerald-600",
  copilot: "text-blue-600",
  anthropic: "text-orange-600",
  openai: "text-violet-600",
  gemini: "text-pink-600",
};

const TOOL_BORDER_COLORS: Record<string, string> = {
  devin: "border-emerald-500",
  copilot: "border-blue-500",
  anthropic: "border-orange-500",
  openai: "border-violet-500",
  gemini: "border-pink-500",
};

const TOOL_LABELS: Record<string, string> = {
  devin: "Devin",
  copilot: "Copilot Autofix",
  anthropic: "Anthropic (claude-opus-4-6)",
  openai: "OpenAI (gpt-5.3-codex)",
  gemini: "Google (gemini-3.1-pro-preview)",
};

const EVENT_ICONS: Record<string, string> = {
  scan_started: "\ud83d\udd0d",
  session_created: "\ud83d\ude80",
  analyzing: "\ud83d\udd2c",
  fix_pushed: "\u2705",
  codeql_verified: "\ud83d\udee1\ufe0f",
  batch_complete: "\ud83d\udce6",
  remediation_complete: "\ud83c\udfc1",
  suggestion_created: "\ud83d\udca1",
  waiting_human: "\u23f3",
  suggestion_accepted: "\ud83d\udc64",
  api_call_sent: "\ud83d\udce1",
  patch_generated: "\ud83d\udd27",
  patch_applied: "\ud83d\udcdd",
  alert_triaged: "\ud83d\udccb",
  alert_skipped: "\u23ed\ufe0f",
  autofix_triggered: "\ud83e\udd16",
  autofix_result: "\ud83d\udcca",
  error: "\u274c",
  cancelled: "\ud83d\uded1",
  codeql_waiting: "\u23f3",
  codeql_ready: "\u2705",
  codeql_timeout: "\u26a0\ufe0f",
};

const SEVERITY_CONFIG = [
  { key: "critical", label: "Critical", color: "bg-red-500", borderColor: "border-red-500" },
  { key: "high", label: "High", color: "bg-orange-500", borderColor: "border-orange-500" },
  { key: "medium", label: "Medium", color: "bg-yellow-500", borderColor: "border-yellow-500" },
  { key: "low", label: "Low", color: "bg-blue-500", borderColor: "border-blue-500" },
];

const POLL_INTERVAL_MS = 3000;

// ---------------------------------------------------------------------------
// Pricing constants (mirrored from backend)
// ---------------------------------------------------------------------------
const DEVIN_ACU_PER_SESSION = 0.09;
const DEVIN_COST_PER_ACU = 2.0;
const COPILOT_COST_PER_REQUEST = 0.04;
const ESTIMATED_TOKENS_PER_ALERT = 4500; // fallback when no baseline token data

const API_TOOL_PRICING: Record<string, { model: string; inputPerMtok: number; outputPerMtok: number }> = {
  anthropic: { model: "claude-opus-4-6", inputPerMtok: 5.0, outputPerMtok: 25.0 },
  openai: { model: "gpt-5.3-codex", inputPerMtok: 1.75, outputPerMtok: 14.0 },
  gemini: { model: "gemini-3.1-pro-preview", inputPerMtok: 2.0, outputPerMtok: 12.0 },
};

/**
 * Compute cost estimates client-side for the given filtered alerts.
 * This reacts to severity selection changes without a backend round-trip.
 */
function computeFilteredCostEstimates(
  filteredAlerts: Alert[],
  baselineEstimatedTokens: number,
  baselineTotalAlerts: number,
): Record<string, CostEstimate> {
  const alertCount = filteredAlerts.length;
  if (alertCount === 0) return {};

  const uniqueFiles = new Set(filteredAlerts.map((a) => a.file_path));
  const fileCount = uniqueFiles.size;

  // Scale baseline token estimate proportionally to filtered alert count.
  // If no baseline estimate, fall back to per-alert heuristic.
  let estimatedInputTokens: number;
  if (baselineEstimatedTokens > 0 && baselineTotalAlerts > 0) {
    estimatedInputTokens = Math.round(
      (baselineEstimatedTokens / baselineTotalAlerts) * alertCount
    );
  } else {
    estimatedInputTokens = alertCount * ESTIMATED_TOKENS_PER_ALERT;
  }
  // Output tokens estimated as ~equal to input tokens
  const estimatedOutputTokens = estimatedInputTokens;

  const estimates: Record<string, CostEstimate> = {};

  // Devin: ACU-based, 1 session per unique file
  const sessionCount = fileCount > 0 ? fileCount : alertCount;
  const estimatedAcus = sessionCount * DEVIN_ACU_PER_SESSION;
  const devinCost = estimatedAcus * DEVIN_COST_PER_ACU;
  estimates.devin = {
    model: "Devin (ACU-based)",
    pricing_type: "acu",
    total_cost_usd: parseFloat(devinCost.toFixed(4)),
    alerts_processed: alertCount,
    estimated_acus: parseFloat(estimatedAcus.toFixed(2)),
    cost_per_acu_usd: DEVIN_COST_PER_ACU,
    assumption:
      fileCount > 0
        ? `Assumes ~${DEVIN_ACU_PER_SESSION} ACU per session, ${sessionCount} sessions (${alertCount} alerts grouped into ${fileCount} files)`
        : `Assumes ~${DEVIN_ACU_PER_SESSION} ACU per session (1 session per unique file)`,
    estimated_input_tokens: 0,
    estimated_output_tokens: 0,
    input_cost_usd: 0,
    output_cost_usd: 0,
    pricing: {},
    cost_per_request_usd: 0,
  };

  // Copilot: flat $0.04 per alert
  const copilotCost = alertCount * COPILOT_COST_PER_REQUEST;
  estimates.copilot = {
    model: "Copilot Autofix",
    pricing_type: "per_request",
    total_cost_usd: parseFloat(copilotCost.toFixed(4)),
    alerts_processed: alertCount,
    cost_per_request_usd: COPILOT_COST_PER_REQUEST,
    assumption: null,
    estimated_input_tokens: 0,
    estimated_output_tokens: 0,
    input_cost_usd: 0,
    output_cost_usd: 0,
    pricing: {},
    estimated_acus: 0,
    cost_per_acu_usd: 0,
  };

  // API tools: token-based pricing
  for (const [tool, pricing] of Object.entries(API_TOOL_PRICING)) {
    const inputCost = (estimatedInputTokens / 1_000_000) * pricing.inputPerMtok;
    const outputCost = (estimatedOutputTokens / 1_000_000) * pricing.outputPerMtok;
    const totalCost = inputCost + outputCost;
    estimates[tool] = {
      model: pricing.model,
      pricing_type: "token",
      total_cost_usd: parseFloat(totalCost.toFixed(4)),
      estimated_input_tokens: estimatedInputTokens,
      estimated_output_tokens: estimatedOutputTokens,
      input_cost_usd: parseFloat(inputCost.toFixed(4)),
      output_cost_usd: parseFloat(outputCost.toFixed(4)),
      pricing: {
        input_per_mtok_usd: pricing.inputPerMtok,
        output_per_mtok_usd: pricing.outputPerMtok,
      },
      alerts_processed: alertCount,
      assumption: null,
      cost_per_request_usd: 0,
      estimated_acus: 0,
      cost_per_acu_usd: 0,
    };
  }

  return estimates;
}

// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}min`;
  return `${(ms / 3600000).toFixed(1)}hr`;
}

function MetadataBadges({ metadata }: { metadata: Record<string, unknown> }) {
  if (!metadata || Object.keys(metadata).length === 0) return null;

  const badges: { label: string; value: string }[] = [];

  if (metadata.model) badges.push({ label: "Model", value: String(metadata.model) });
  if (metadata.latency_ms != null) badges.push({ label: "Latency", value: `${metadata.latency_ms}ms` });
  if (metadata.input_tokens != null) badges.push({ label: "In", value: `${Number(metadata.input_tokens).toLocaleString()} tok` });
  if (metadata.output_tokens != null) badges.push({ label: "Out", value: `${Number(metadata.output_tokens).toLocaleString()} tok` });
  if (metadata.prompt_tokens != null) badges.push({ label: "Prompt", value: `${Number(metadata.prompt_tokens).toLocaleString()} tok` });
  if (metadata.event_cost_usd != null && Number(metadata.event_cost_usd) > 0)
    badges.push({ label: "Cost", value: `$${Number(metadata.event_cost_usd).toFixed(4)}` });
  if (metadata.cumulative_cost_usd != null && Number(metadata.cumulative_cost_usd) > 0)
    badges.push({ label: "Running Total", value: `$${Number(metadata.cumulative_cost_usd).toFixed(4)}` });
  if (metadata.commit_sha) badges.push({ label: "Commit", value: String(metadata.commit_sha).slice(0, 8) });
  if (metadata.severity) badges.push({ label: "Severity", value: String(metadata.severity) });
  if (metadata.branch) badges.push({ label: "Branch", value: String(metadata.branch) });
  if (metadata.completed != null) badges.push({ label: "Fixed", value: String(metadata.completed) });
  if (metadata.failed != null) badges.push({ label: "Failed", value: String(metadata.failed) });

  if (badges.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {badges.map((b) => (
        <span key={b.label} className="inline-flex items-center gap-0.5 rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono">
          <span className="text-muted-foreground">{b.label}:</span> {b.value}
        </span>
      ))}
    </div>
  );
}

function TimelineEvent({ event, maxOffset, isFirstRow }: { event: ReplayEvent; maxOffset: number; isFirstRow: boolean }) {
  const leftPct = maxOffset > 0 ? (event.timestamp_offset_ms / maxOffset) * 100 : 0;
  const icon = EVENT_ICONS[event.event_type] || "\u2022";
  const meta = event.metadata || {};

  // For the first row, show tooltip below to avoid being clipped by the card boundary.
  // For all other rows, show above as usual.
  const tooltipPosition = isFirstRow
    ? "absolute top-full left-1/2 -translate-x-1/2 mt-2 hidden group-hover:block z-10 w-80"
    : "absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-10 w-80";

  return (
    <div
      className="absolute group"
      style={{ left: `${Math.min(leftPct, 98)}%`, top: 0 }}
    >
      <div className="relative cursor-pointer">
        <span className="text-sm">{icon}</span>
        <div className={tooltipPosition}>
          <div className="rounded-lg border bg-popover p-3 text-xs shadow-md">
            <p className="font-medium">{event.event_type.replace(/_/g, " ")}</p>
            <p className="text-muted-foreground mt-1">{event.detail}</p>
            {event.alert_number && (
              <p className="text-muted-foreground">Alert #{event.alert_number}</p>
            )}
            {"file_path" in meta && meta.file_path != null && (
              <p className="text-muted-foreground font-mono truncate">{String(meta.file_path)}</p>
            )}
            <MetadataBadges metadata={meta} />
            <p className="text-muted-foreground mt-1">
              T+{formatDuration(event.timestamp_offset_ms)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live Replay View (auto-advance for live, full view when completed)
// ---------------------------------------------------------------------------

function LiveReplayView({ run, isLive }: { run: ReplayRunWithEvents; isLive: boolean }) {
  const maxOffset = run.total_duration_ms || Math.max(...run.events.map((e) => e.timestamp_offset_ms), 1);

  const visibleEvents = run.events;

  // Count fixes per tool (deduplicate by alert_number)
  const fixCounts: Record<string, number> = {};
  for (const tool of run.tools) {
    const fixedAlerts = new Set(
      visibleEvents
        .filter(
          (e) =>
            e.tool === tool &&
            e.alert_number != null &&
            (e.event_type === "fix_pushed" ||
              e.event_type === "codeql_verified" ||
              e.event_type === "suggestion_accepted" ||
              e.event_type === "patch_applied")
        )
        .map((e) => e.alert_number)
    );
    fixCounts[tool] = fixedAlerts.size;
  }

  // Group events by tool
  const eventsByTool: Record<string, ReplayEvent[]> = {};
  for (const tool of run.tools) {
    eventsByTool[tool] = run.events.filter((e) => e.tool === tool);
  }

  // Latest event per tool
  const latestPerTool: Record<string, ReplayEvent | null> = {};
  for (const tool of run.tools) {
    const toolEvents = eventsByTool[tool] || [];
    latestPerTool[tool] = toolEvents.length > 0 ? toolEvents[toolEvents.length - 1] : null;
  }

  // Running cost total
  const totalCost = visibleEvents.reduce((sum, e) => sum + (e.cost_usd || 0), 0);

  // Extract Devin session info from events (session_created / session_complete / analyzing)
  const devinSessions = useMemo(() => {
    const sessions: { id: string; url: string; filePath: string; status: string }[] = [];
    const seen = new Set<string>();
    for (const event of visibleEvents) {
      if (event.tool !== "devin") continue;
      const meta = event.metadata || {};
      const sid = meta.session_id as string | undefined;
      if (!sid || seen.has(sid)) {
        // Update status if we see a terminal event for a known session
        if (sid && seen.has(sid) && (event.event_type === "session_complete" || event.event_type === "cancelled" || event.event_type === "polling_timeout")) {
          const existing = sessions.find((s) => s.id === sid);
          if (existing) {
            if (event.event_type === "session_complete") {
              existing.status = String(meta.status ?? "done");
            } else if (event.event_type === "cancelled") {
              existing.status = "cancelled";
            } else if (event.event_type === "polling_timeout") {
              existing.status = "timeout";
            }
          }
        }
        continue;
      }
      seen.add(sid);
      sessions.push({
        id: sid,
        url: (meta.session_url as string) || `https://app.devin.ai/sessions/${sid}`,
        filePath: String(meta.file_path ?? ""),
        status: event.event_type === "session_complete" ? String(meta.status ?? "done") : "running",
      });
    }
    return sessions;
  }, [visibleEvents]);

  // Determine tool completion status
  const toolStatus = (tool: string): "running" | "completed" | "error" | "waiting" | "cancelled" => {
    const toolEvents = eventsByTool[tool] || [];
    if (toolEvents.length === 0) return "waiting";
    const last = toolEvents[toolEvents.length - 1];
    if (last.event_type === "remediation_complete") return "completed";
    if (last.event_type === "cancelled") return "cancelled";
    if (last.event_type === "error" && toolEvents.length <= 2) return "error";
    return "running";
  };

  const logContainerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll only the event log container (not the whole page) in live mode
  useEffect(() => {
    if (isLive && logContainerRef.current) {
      const el = logContainerRef.current;
      el.scrollTop = el.scrollHeight;
    }
  }, [isLive, visibleEvents.length]);

  return (
    <div className="space-y-6">
      {/* Status Bar */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isLive ? (
                <Badge className="bg-red-500 text-white animate-pulse">LIVE</Badge>
              ) : run.status === "cancelled" ? (
                <Badge className="bg-amber-500 text-white">Cancelled</Badge>
              ) : (
                <Badge variant="secondary">Completed</Badge>
              )}
              <span className="text-sm text-muted-foreground">
                {visibleEvents.length} events across {run.tools.length} tools
              </span>
            </div>
            <div className="flex items-center gap-4">
              {totalCost > 0 && (
                <span className="text-sm font-mono font-semibold text-amber-600">
                  ${totalCost.toFixed(4)}
                </span>
              )}
              {run.total_cost_usd > 0 && run.total_cost_usd !== totalCost && (
                <span className="text-xs text-muted-foreground font-mono">
                  (Total: ${run.total_cost_usd.toFixed(4)})
                </span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tool Cards */}
      <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-5">
        {run.tools.map((tool) => {
          const toolEvents = eventsByTool[tool] || [];
          const totalTokens = toolEvents.reduce((sum, e) => {
            const m = e.metadata || {};
            return sum + (Number(m.input_tokens) || 0) + (Number(m.output_tokens) || 0);
          }, 0);
          const totalLatency = toolEvents.reduce((sum, e) => {
            return sum + (Number(e.metadata?.latency_ms) || 0);
          }, 0);
          const toolCost = toolEvents.reduce((sum, e) => sum + (e.cost_usd || 0), 0);
          const status = toolStatus(tool);

          return (
            <Card key={tool} className={`border-l-4 ${TOOL_BORDER_COLORS[tool] || "border-gray-500"}`}>
              <CardContent className="pt-4 pb-4">
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className={`font-medium text-sm truncate ${TOOL_TEXT_COLORS[tool] || ""}`}>
                        {TOOL_LABELS[tool] || tool}
                      </p>
                      {status === "running" && isLive && (
                        <span className="relative flex h-2 w-2">
                          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${TOOL_COLORS[tool] || "bg-gray-500"}`} />
                          <span className={`relative inline-flex rounded-full h-2 w-2 ${TOOL_COLORS[tool] || "bg-gray-500"}`} />
                        </span>
                      )}
                      {status === "completed" && (
                        <span className="text-xs text-emerald-600">{"\u2713"}</span>
                      )}
                      {status === "error" && (
                        <span className="text-xs text-destructive">{"\u2717"}</span>
                      )}
                      {status === "cancelled" && (
                        <span className="text-xs text-amber-600">{"\u23f9"}</span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5 truncate">
                      {latestPerTool[tool]?.event_type.replace(/_/g, " ") || "Waiting..."}
                    </p>
                    {totalTokens > 0 && (
                      <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
                        {totalTokens.toLocaleString()} tokens
                        {totalLatency > 0 && ` \u00b7 ${formatDuration(totalLatency)} LLM`}
                      </p>
                    )}
                    {toolCost > 0 && (
                      <p className="text-[10px] font-mono mt-0.5 text-amber-600">
                        ${toolCost.toFixed(4)} spent
                      </p>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-2xl font-bold">{fixCounts[tool] || 0}</p>
                    <p className="text-xs text-muted-foreground">fixes</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Timeline Lanes */}
      <Card>
        <CardHeader>
          <CardTitle>Remediation Timeline</CardTitle>
          <CardDescription>
            {isLive ? "Events appear in real-time." : "Hover over events for details."} Each row represents a tool.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            {run.tools.map((tool, toolIndex) => {
              const toolEvents = eventsByTool[tool] || [];

              return (
                <div key={tool} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className={`h-3 w-3 rounded-full ${TOOL_COLORS[tool] || "bg-gray-500"}`} />
                    <span className="text-sm font-medium">{TOOL_LABELS[tool] || tool}</span>
                    <Badge variant="outline" className="text-xs">
                      {toolEvents.length} events
                    </Badge>
                  </div>
                  <div className="relative h-8 bg-muted/50 rounded-md overflow-visible">
                    {toolEvents.map((event) => (
                      <TimelineEvent
                        key={event.id}
                        event={event}
                        maxOffset={maxOffset}
                        isFirstRow={toolIndex === 0}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Time markers */}
          <div className="flex justify-between mt-2 text-xs text-muted-foreground">
            <span>0s</span>
            <span>{formatDuration(maxOffset * 0.25)}</span>
            <span>{formatDuration(maxOffset * 0.5)}</span>
            <span>{formatDuration(maxOffset * 0.75)}</span>
            <span>{formatDuration(maxOffset)}</span>
          </div>
        </CardContent>
      </Card>

      {/* Devin Sessions */}
      {devinSessions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Devin Sessions</CardTitle>
            <CardDescription>
              {devinSessions.length} session{devinSessions.length !== 1 ? "s" : ""} created
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {devinSessions.map((session) => (
                <div
                  key={session.id}
                  className="flex items-center gap-3 py-2 px-3 rounded-md border text-sm"
                >
                  <span className="shrink-0">
                    {session.status === "running" ? (
                      <span className="relative flex h-2.5 w-2.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
                      </span>
                    ) : session.status === "error" || session.status === "suspended" ? (
                      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
                    ) : session.status === "timeout" || session.status === "cancelled" ? (
                      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
                    ) : (
                      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
                    )}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="font-mono text-xs truncate">{session.filePath}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {session.status === "running" ? "In progress..." : session.status === "timeout" ? "Timed out" : session.status === "cancelled" ? "Cancelled" : session.status}
                    </p>
                  </div>
                  <a
                    href={session.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-600 hover:underline shrink-0"
                  >
                    View session
                  </a>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Event Log */}
      <Card>
        <CardHeader>
          <CardTitle>Event Log</CardTitle>
          <CardDescription>
            {visibleEvents.length} events{isLive ? " (auto-scrolling)" : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div ref={logContainerRef} className="max-h-96 overflow-y-auto space-y-1">
            {visibleEvents.map((event) => {
              const meta = event.metadata || {};
              const rawResponse = meta.raw_response as string | Record<string, unknown> | undefined;
              return (
                <div key={event.id} className="py-1.5 text-sm">
                  <div className="flex items-start gap-3">
                    <span className="font-mono text-xs text-muted-foreground w-16 shrink-0">
                      +{formatDuration(event.timestamp_offset_ms)}
                    </span>
                    <span className={`font-medium w-20 shrink-0 text-xs ${TOOL_TEXT_COLORS[event.tool] || ""}`}>
                      {TOOL_LABELS[event.tool] || event.tool}
                    </span>
                    <span className="w-4">{EVENT_ICONS[event.event_type] || "\u2022"}</span>
                    <div className="flex-1 min-w-0">
                      <span className="text-muted-foreground">{event.detail}</span>
                      <MetadataBadges metadata={meta} />
                    </div>
                    {event.cost_usd > 0 && (
                      <span className="text-xs font-mono text-amber-600 shrink-0">
                        ${event.cost_usd.toFixed(4)}
                      </span>
                    )}
                  </div>
                  {rawResponse && (
                    <details className="ml-[9.5rem] mt-1">
                      <summary className="text-[10px] text-blue-600 cursor-pointer hover:underline select-none">
                        View raw response
                      </summary>
                      <pre className="mt-1 p-2 rounded bg-muted text-[11px] overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap break-all">
                        {typeof rawResponse === "string"
                          ? rawResponse
                          : JSON.stringify(rawResponse, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              );
            })}
            {visibleEvents.length === 0 && (
              <p className="text-center text-muted-foreground py-4">
                Waiting for events...
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function RemediationPage() {
  const { selectedRepo } = useRepo();

  // Setup state
  const [baselineAlerts, setBaselineAlerts] = useState<Alert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [selectedSeverities, setSelectedSeverities] = useState<Set<string>>(
    new Set(["critical", "high", "medium", "low"])
  );
  const [baselineTokenData, setBaselineTokenData] = useState<{ tokens: number; totalAlerts: number }>({ tokens: 0, totalAlerts: 0 });
  const [error, setError] = useState<string | null>(null);

  // Benchmark state
  const [benchmarkRunning, setBenchmarkRunning] = useState(false);
  const [runId, setRunId] = useState<number | null>(null);
  const [liveRun, setLiveRun] = useState<ReplayRunWithEvents | null>(null);
  const [setupCollapsed, setSetupCollapsed] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Severity counts from baseline alerts
  const severityCounts: Record<string, number> = {};
  for (const alert of baselineAlerts) {
    const sev = alert.severity.toLowerCase();
    severityCounts[sev] = (severityCounts[sev] || 0) + 1;
  }

  // Filtered alerts (used for cost estimation, file count, and alert count)
  const filteredAlerts = baselineAlerts.filter((a) =>
    selectedSeverities.has(a.severity.toLowerCase())
  );
  const filteredAlertCount = filteredAlerts.length;

  // Unique file count for filtered alerts
  const filteredFiles = new Set(filteredAlerts.map((a) => a.file_path));

  // Client-side cost estimates — recomputed whenever severity selection changes
  const costEstimates = useMemo(
    () =>
      computeFilteredCostEstimates(
        filteredAlerts,
        baselineTokenData.tokens,
        baselineTokenData.totalAlerts,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filteredAlerts.length, filteredFiles.size, baselineTokenData.tokens, baselineTokenData.totalAlerts],
  );

  // Load baseline alerts
  const loadBaselineAlerts = useCallback(async () => {
    setAlertsLoading(true);
    setError(null);
    try {
      const data = await api.getLiveAlerts("baseline", "open", selectedRepo);
      setBaselineAlerts(data.alerts);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load alerts";
      if (!msg.includes("404")) setError(msg);
    } finally {
      setAlertsLoading(false);
    }
  }, [selectedRepo]);

  // Load baseline token estimate (for scaling API cost estimates)
  const loadBaselineTokenData = useCallback(async () => {
    try {
      const data = await api.compareLatest(selectedRepo);
      if (data.baseline) {
        setBaselineTokenData({
          tokens: data.baseline.estimated_prompt_tokens ?? 0,
          totalAlerts: data.baseline.open ?? 0,
        });
      }
    } catch {
      // Not critical — falls back to per-alert heuristic
    }
  }, [selectedRepo]);

  useEffect(() => {
    if (!selectedRepo) return;
    loadBaselineAlerts();
    loadBaselineTokenData();
  }, [selectedRepo, loadBaselineAlerts, loadBaselineTokenData]);

  // Poll for live run updates
  useEffect(() => {
    if (runId == null) return;

    const poll = async () => {
      try {
        const data = await api.getReplayRun(runId, selectedRepo);
        setLiveRun(data);
        if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
          setBenchmarkRunning(false);
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch {
        // Ignore transient errors during polling
      }
    };

    // Initial fetch
    poll();

    // Start polling
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runId, selectedRepo]);

  // Toggle severity checkbox
  const toggleSeverity = (severity: string) => {
    setSelectedSeverities((prev) => {
      const next = new Set(prev);
      if (next.has(severity)) {
        next.delete(severity);
      } else {
        next.add(severity);
      }
      return next;
    });
  };

  // Run benchmark
  const runBenchmark = async () => {
    if (filteredAlertCount === 0) return;
    setBenchmarkRunning(true);
    setSetupCollapsed(true);
    setError(null);
    setLiveRun(null);

    try {
      const result = await api.triggerBenchmark(
        Array.from(selectedSeverities),
        selectedRepo,
      );
      setRunId(result.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start benchmark");
      setBenchmarkRunning(false);
      setSetupCollapsed(false);
    }
  };

  // Reset to setup view
  const resetBenchmark = () => {
    setBenchmarkRunning(false);
    setSetupCollapsed(false);
    setRunId(null);
    setLiveRun(null);
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  if (!selectedRepo) {
    return (
      <div className="flex flex-col items-center justify-center py-24 space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">No repo selected</h1>
        <p className="text-muted-foreground">Add and select a repository to run remediation.</p>
        <Link href="/repos">
          <Button>Go to Repos</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Remediation Benchmark</h1>
          <p className="text-muted-foreground mt-1">
            {setupCollapsed
              ? benchmarkRunning
                ? `Running ${filteredAlertCount} alerts across 5 tools`
                : liveRun?.status === "cancelled"
                  ? "Benchmark was cancelled"
                  : `Completed ${filteredAlertCount} alerts across 5 tools`
              : "Select alert severities and run all remediation tools simultaneously."}
          </p>
        </div>
        {setupCollapsed && (
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-sm font-mono">
              {filteredAlertCount} alerts
            </Badge>
            {benchmarkRunning && (
              <Badge className="bg-red-500 text-white animate-pulse">LIVE</Badge>
            )}
            {benchmarkRunning && runId != null && (
              <Button
                variant="destructive"
                size="sm"
                onClick={async () => {
                  try {
                    await api.cancelBenchmark(runId, selectedRepo);
                    // Stop polling and mark as not running, but keep the
                    // replay view visible so the user sees the cancelled state.
                    setBenchmarkRunning(false);
                    if (pollRef.current) {
                      clearInterval(pollRef.current);
                      pollRef.current = null;
                    }
                    // Do one final fetch to get the updated status
                    const updated = await api.getReplayRun(runId, selectedRepo);
                    setLiveRun(updated);
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "Failed to cancel benchmark");
                  }
                }}
              >
                Stop Benchmark
              </Button>
            )}
            {!benchmarkRunning && (
              <Button variant="outline" size="sm" onClick={resetBenchmark}>
                New Benchmark
              </Button>
            )}
          </div>
        )}
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Setup Section (collapsible) */}
      {!setupCollapsed && (
        <div className="space-y-6">
          {/* Severity Selection */}
          <Card>
            <CardHeader>
              <CardTitle>Alert Severity Filter</CardTitle>
              <CardDescription>
                {baselineAlerts.length} open baseline alerts &middot; {filteredAlertCount} selected
                &middot; {filteredFiles.size} unique files
              </CardDescription>
            </CardHeader>
            <CardContent>
              {alertsLoading && baselineAlerts.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">Loading alerts...</p>
              ) : baselineAlerts.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">
                  No open baseline alerts found. Run a scan first.
                </p>
              ) : (
                <div className="grid gap-3 md:grid-cols-4">
                  {SEVERITY_CONFIG.map((sev) => {
                    const count = severityCounts[sev.key] || 0;
                    const isSelected = selectedSeverities.has(sev.key);

                    return (
                      <label
                        key={sev.key}
                        className={`flex items-center gap-3 p-4 rounded-lg border-2 cursor-pointer transition-all ${
                          isSelected
                            ? `${sev.borderColor} bg-muted/50`
                            : "border-border hover:border-muted-foreground/30"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSeverity(sev.key)}
                          className="h-4 w-4 rounded"
                        />
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className={`h-2.5 w-2.5 rounded-full ${sev.color}`} />
                            <span className="font-medium text-sm">{sev.label}</span>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {count} alert{count !== 1 ? "s" : ""}
                          </p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Cost Estimates Summary */}
          <Card>
            <CardHeader>
              <CardTitle>Estimated Remediation Cost</CardTitle>
              <CardDescription>
                Cost estimates for {filteredAlertCount} alerts across all 5 tools
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 md:grid-cols-5">
                {["devin", "copilot", "anthropic", "openai", "gemini"].map((tool) => {
                  const estimate = costEstimates[tool];
                  return (
                    <div
                      key={tool}
                      className={`border rounded-lg p-3 border-l-4 ${TOOL_BORDER_COLORS[tool] || "border-gray-500"}`}
                    >
                      <p className={`font-medium text-sm ${TOOL_TEXT_COLORS[tool] || ""}`}>
                        {TOOL_LABELS[tool] || tool}
                      </p>
                      {estimate ? (
                        <div className="mt-1.5 space-y-0.5">
                          <p className="text-lg font-bold font-mono">
                            ${estimate.total_cost_usd.toFixed(4)}
                          </p>
                          {estimate.pricing_type === "acu" && (
                            <p className="text-[10px] text-muted-foreground">
                              {estimate.estimated_acus.toFixed(2)} ACU @ ${estimate.cost_per_acu_usd}/ACU
                            </p>
                          )}
                          {estimate.pricing_type === "per_request" && (
                            <p className="text-[10px] text-muted-foreground">
                              {estimate.alerts_processed} alerts @ ${estimate.cost_per_request_usd}/req
                            </p>
                          )}
                          {estimate.pricing_type === "token" && (
                            <>
                              <p className="text-[10px] text-muted-foreground">
                                ~{estimate.estimated_input_tokens.toLocaleString()} input tokens
                              </p>
                              <p className="text-[10px] text-muted-foreground">
                                ~{estimate.estimated_output_tokens.toLocaleString()} output tokens <span className="italic">(est.)</span>
                              </p>
                            </>
                          )}
                          {estimate.assumption && (
                            <p className="text-[10px] text-muted-foreground italic">
                              {estimate.assumption}
                            </p>
                          )}
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground mt-1">
                          No estimate available
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          {/* Run Benchmark Button */}
          <div className="flex items-center justify-center">
            <Button
              size="lg"
              className="px-12 py-6 text-lg"
              onClick={runBenchmark}
              disabled={filteredAlertCount === 0 || benchmarkRunning || alertsLoading}
            >
              {benchmarkRunning
                ? "Benchmark Running..."
                : `Run Benchmark (${filteredAlertCount} alerts \u00d7 5 tools)`}
            </Button>
          </div>
        </div>
      )}

      {/* Live Replay View */}
      {liveRun && (
        <LiveReplayView run={liveRun} isLive={benchmarkRunning} />
      )}

      {/* Skeleton loading while benchmark initialises (branch creation, etc.) */}
      {benchmarkRunning && !liveRun && (
        <div className="space-y-6">
          {/* Status bar skeleton */}
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Skeleton className="h-3 w-3 rounded-full" />
                  <Skeleton className="h-4 w-48" />
                </div>
                <Skeleton className="h-4 w-24" />
              </div>
            </CardContent>
          </Card>

          {/* Remediation Timeline skeleton */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Skeleton className="h-5 w-44" />
              </div>
              <Skeleton className="h-3 w-64 mt-1" />
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i} className="flex items-center gap-3">
                    <Skeleton className="h-4 w-20" />
                    <div className="flex-1 flex items-center gap-1">
                      {Array.from({ length: 8 }).map((_, j) => (
                        <Skeleton key={j} className="h-5 w-5 rounded" />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <Skeleton className="h-3 w-full mt-4" />
            </CardContent>
          </Card>

          {/* Event Log skeleton */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Skeleton className="h-5 w-24" />
              </div>
              <Skeleton className="h-3 w-40 mt-1" />
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="flex items-start gap-3">
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-5 w-5 rounded" />
                    <div className="flex-1 space-y-1">
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-3 w-3/4" />
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Subtle status text */}
          <p className="text-center text-sm text-muted-foreground animate-pulse">
            Setting up benchmark &mdash; creating branches and preparing tools&hellip;
          </p>
        </div>
      )}
    </div>
  );
}
