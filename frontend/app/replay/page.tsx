"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { api, type ReplayRunWithEvents, type ReplayRun, type ReplayEvent } from "@/lib/api";
import { useRepo } from "@/lib/repo-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

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
  scan_started: "🔍",
  session_created: "🚀",
  analyzing: "🔬",
  fix_pushed: "✅",
  codeql_verified: "🛡️",
  batch_complete: "📦",
  remediation_complete: "🏁",
  suggestion_created: "💡",
  waiting_human: "⏳",
  suggestion_accepted: "👤",
  api_call_sent: "📡",
  patch_generated: "🔧",
  patch_applied: "📝",
  alert_triaged: "📋",
  alert_skipped: "⏭️",
  error: "❌",
};

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

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}min`;
  return `${(ms / 3600000).toFixed(1)}hr`;
}

function TimelineEvent({ event, maxOffset }: { event: ReplayEvent; maxOffset: number }) {
  const leftPct = maxOffset > 0 ? (event.timestamp_offset_ms / maxOffset) * 100 : 0;
  const icon = EVENT_ICONS[event.event_type] || "•";
  const meta = event.metadata || {};

  return (
    <div
      className="absolute group"
      style={{ left: `${Math.min(leftPct, 98)}%`, top: 0 }}
    >
      <div className="relative cursor-pointer">
        <span className="text-sm">{icon}</span>
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-10 w-80">
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

function PlaybackTimeline({ run }: { run: ReplayRunWithEvents }) {
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const maxOffset = run.total_duration_ms || 1;

  // Group events by tool
  const eventsByTool: Record<string, ReplayEvent[]> = {};
  for (const tool of run.tools) {
    eventsByTool[tool] = run.events.filter((e) => e.tool === tool);
  }

  // Visible events (before current playback time)
  const visibleEvents = run.events.filter((e) => e.timestamp_offset_ms <= currentTime);

  // Count fixes per tool at current time (deduplicate by alert_number)
  const fixCounts: Record<string, number> = {};
  for (const tool of run.tools) {
    const fixedAlerts = new Set(
      visibleEvents
        .filter((e) => e.tool === tool && e.alert_number != null && (e.event_type === "fix_pushed" || e.event_type === "codeql_verified" || e.event_type === "suggestion_accepted" || e.event_type === "patch_applied"))
        .map((e) => e.alert_number)
    );
    fixCounts[tool] = fixedAlerts.size;
  }

  const play = useCallback(() => {
    setPlaying(true);
  }, []);

  const pause = useCallback(() => {
    setPlaying(false);
  }, []);

  const reset = useCallback(() => {
    setPlaying(false);
    setCurrentTime(0);
  }, []);

  useEffect(() => {
    if (playing) {
      const stepMs = 50;
      const increment = (maxOffset / 30000) * stepMs * speed; // 30s real time for full playback at 1x
      intervalRef.current = setInterval(() => {
        setCurrentTime((prev) => {
          const next = prev + increment;
          if (next >= maxOffset) {
            setPlaying(false);
            return maxOffset;
          }
          return next;
        });
      }, stepMs);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [playing, speed, maxOffset]);

  const progressPct = maxOffset > 0 ? (currentTime / maxOffset) * 100 : 0;

  // Latest event per tool
  const latestPerTool: Record<string, ReplayEvent | null> = {};
  for (const tool of run.tools) {
    const toolVisible = visibleEvents.filter((e) => e.tool === tool);
    latestPerTool[tool] = toolVisible.length > 0 ? toolVisible[toolVisible.length - 1] : null;
  }

  return (
    <div className="space-y-6">
      {/* Playback Controls */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center gap-4">
            {!playing ? (
              <Button onClick={play} size="sm">
                ▶ Play
              </Button>
            ) : (
              <Button onClick={pause} size="sm" variant="outline">
                ⏸ Pause
              </Button>
            )}
            <Button onClick={reset} size="sm" variant="outline">
              ⏮ Reset
            </Button>

            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">Speed:</span>
              {[1, 2, 5, 10].map((s) => (
                <Button
                  key={s}
                  size="sm"
                  variant={speed === s ? "default" : "outline"}
                  onClick={() => setSpeed(s)}
                  className="h-7 px-2 text-xs"
                >
                  {s}x
                </Button>
              ))}
            </div>

            <div className="ml-auto flex items-center gap-4">
              {/* Running cost counter */}
              {(() => {
                const currentCost = visibleEvents.reduce((sum, e) => sum + (e.cost_usd || 0), 0);
                return currentCost > 0 ? (
                  <span className="text-sm font-mono font-semibold text-amber-600">
                    ${currentCost.toFixed(4)}
                  </span>
                ) : null;
              })()}
              <span className="text-sm font-mono">
                {formatDuration(currentTime)} / {formatDuration(maxOffset)}
              </span>
            </div>
          </div>

          {/* Progress Bar */}
          <div className="mt-4 relative">
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <input
              type="range"
              min={0}
              max={maxOffset}
              value={currentTime}
              onChange={(e) => setCurrentTime(Number(e.target.value))}
              className="absolute inset-0 w-full h-2 opacity-0 cursor-pointer"
            />
          </div>
        </CardContent>
      </Card>

      {/* Live Counters */}
      <div className="grid gap-4 md:grid-cols-3">
        {run.tools.map((tool) => {
          // Aggregate token/latency stats from visible events for this tool
          const toolVisible = visibleEvents.filter((e) => e.tool === tool);
          const totalTokens = toolVisible.reduce((sum, e) => {
            const m = e.metadata || {};
            return sum + (Number(m.input_tokens) || 0) + (Number(m.output_tokens) || 0);
          }, 0);
          const totalLatency = toolVisible.reduce((sum, e) => {
            return sum + (Number(e.metadata?.latency_ms) || 0);
          }, 0);

          // Running cost for this tool at current time
          const toolCost = toolVisible.reduce((sum, e) => sum + (e.cost_usd || 0), 0);

          return (
            <Card key={tool} className={`border-l-4 ${TOOL_BORDER_COLORS[tool] || "border-gray-500"}`}>
              <CardContent className="pt-4 pb-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className={`font-medium ${TOOL_TEXT_COLORS[tool] || ""}`}>
                      {TOOL_LABELS[tool] || tool}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {latestPerTool[tool]?.event_type.replace(/_/g, " ") || "Waiting..."}
                    </p>
                    {totalTokens > 0 && (
                      <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
                        {totalTokens.toLocaleString()} tokens
                        {totalLatency > 0 && ` · ${formatDuration(totalLatency)} LLM time`}
                      </p>
                    )}
                    {toolCost > 0 && (
                      <p className="text-[10px] font-mono mt-0.5 text-amber-600">
                        ${toolCost.toFixed(4)} spent
                      </p>
                    )}
                  </div>
                  <div className="text-right">
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
            Hover over events to see details. Each row represents a tool.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            {run.tools.map((tool) => {
              const toolEvents = eventsByTool[tool] || [];
              const toolVisibleEvents = toolEvents.filter(
                (e) => e.timestamp_offset_ms <= currentTime
              );

              return (
                <div key={tool} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className={`h-3 w-3 rounded-full ${TOOL_COLORS[tool] || "bg-gray-500"}`} />
                    <span className="text-sm font-medium">{TOOL_LABELS[tool] || tool}</span>
                    <Badge variant="outline" className="text-xs">
                      {toolVisibleEvents.length} / {toolEvents.length} events
                    </Badge>
                  </div>
                  <div className="relative h-8 bg-muted/50 rounded-md overflow-visible">
                    {toolVisibleEvents.map((event) => (
                      <TimelineEvent
                        key={event.id}
                        event={event}
                        maxOffset={maxOffset}
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

      {/* Event Log */}
      <Card>
        <CardHeader>
          <CardTitle>Event Log</CardTitle>
          <CardDescription>
            Showing {visibleEvents.length} of {run.events.length} events
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="max-h-96 overflow-y-auto space-y-1">
            {visibleEvents.map((event) => {
              const meta = event.metadata || {};
              return (
                <div
                  key={event.id}
                  className="flex items-start gap-3 py-1.5 text-sm"
                >
                  <span className="font-mono text-xs text-muted-foreground w-16 shrink-0">
                    +{formatDuration(event.timestamp_offset_ms)}
                  </span>
                  <span className={`font-medium w-20 shrink-0 ${TOOL_TEXT_COLORS[event.tool] || ""}`}>
                    {TOOL_LABELS[event.tool] || event.tool}
                  </span>
                  <span className="w-4">{EVENT_ICONS[event.event_type] || "•"}</span>
                  <div className="flex-1 min-w-0">
                    <span className="text-muted-foreground">{event.detail}</span>
                    <MetadataBadges metadata={meta} />
                  </div>
                </div>
              );
            })}
            {visibleEvents.length === 0 && (
              <p className="text-center text-muted-foreground py-4">
                Press Play to start the replay
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ReplayPage() {
  const { selectedRepo } = useRepo();
  const [runs, setRuns] = useState<ReplayRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<ReplayRunWithEvents | null>(null);
  const [loading, setLoading] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      const data = await api.listReplayRuns(selectedRepo);
      setRuns(data);
    } catch {
      // Ignore errors on initial load
    }
  }, [selectedRepo]);

  useEffect(() => {
    setSelectedRun(null);
    if (selectedRepo) loadRuns();
  }, [loadRuns, selectedRepo]);

  const loadRun = async (runId: number) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getReplayRun(runId, selectedRepo);
      setSelectedRun(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load replay");
    } finally {
      setLoading(false);
    }
  };

  const seedDemo = async () => {
    setSeeding(true);
    setError(null);
    try {
      const result = await api.seedDemoReplay(selectedRepo);
      await loadRuns();
      await loadRun(result.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to seed demo data");
    } finally {
      setSeeding(false);
    }
  };

  if (!selectedRepo) {
    return (
      <div className="flex flex-col items-center justify-center py-24 space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">No repo selected</h1>
        <p className="text-muted-foreground">Add and select a repository to view replays.</p>
        <Link href="/repos">
          <Button>Go to Repos</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Replay</h1>
          <p className="text-muted-foreground mt-1">
            Watch tools race to fix CodeQL alerts side-by-side
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={loadRuns}>
            Refresh
          </Button>
          <Button onClick={seedDemo} disabled={seeding}>
            {seeding ? "Seeding..." : "Seed Demo Data"}
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Run Selector */}
      {runs.length > 0 && !selectedRun && (
        <Card>
          <CardHeader>
            <CardTitle>Available Replays</CardTitle>
            <CardDescription>Select a run to replay</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {runs.map((run) => (
                <button
                  key={run.id}
                  onClick={() => loadRun(run.id)}
                  className="w-full flex items-center justify-between rounded-lg border p-4 hover:bg-muted/50 transition-colors text-left"
                >
                  <div>
                    <p className="font-medium">Run #{run.id}</p>
                    <p className="text-sm text-muted-foreground">
                      {run.repo} &middot; {new Date(run.started_at).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={run.status === "completed" ? "default" : "secondary"}>
                      {run.status}
                    </Badge>
                    {run.total_cost_usd > 0 && (
                      <span className="text-sm font-mono text-amber-600">
                        ${run.total_cost_usd.toFixed(2)}
                      </span>
                    )}
                    <span className="text-sm text-muted-foreground">
                      {run.tools.length} tools
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {runs.length === 0 && !selectedRun && !loading && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <p className="text-lg font-medium text-muted-foreground">No replay data yet</p>
            <p className="text-sm text-muted-foreground mt-1">
              Click &ldquo;Seed Demo Data&rdquo; to create a demo replay, or run a remediation to record real events.
            </p>
          </CardContent>
        </Card>
      )}

      {loading && (
        <Card>
          <CardContent className="flex items-center justify-center py-16">
            <p className="text-muted-foreground animate-pulse">Loading replay data...</p>
          </CardContent>
        </Card>
      )}

      {selectedRun && (
        <div className="space-y-4">
          <Button variant="outline" size="sm" onClick={() => setSelectedRun(null)}>
            ← Back to list
          </Button>
          <PlaybackTimeline run={selectedRun} />
        </div>
      )}
    </div>
  );
}
