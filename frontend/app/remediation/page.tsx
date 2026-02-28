"use client";

import { useEffect, useState, useCallback } from "react";
import {
  api,
  type Alert,
  type DevinSession,
  type ApiRemediationJob,
  type ApiRemediationResponse,
  type ComparisonResult,
} from "@/lib/api";
import { useRepo } from "@/lib/repo-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  running: "default",
  pending: "default",
  completed: "secondary",
  failed: "destructive",
  blocked: "outline",
  stopped: "outline",
};

const API_TOOLS = [
  { key: "anthropic", label: "Anthropic", model: "claude-opus-4-6", color: "bg-orange-500" },
  { key: "openai", label: "OpenAI", model: "gpt-5.3-codex", color: "bg-violet-500" },
  { key: "gemini", label: "Google", model: "gemini-3.1-pro-preview", color: "bg-pink-500" },
];

function getSeverityVariant(severity: string): "default" | "secondary" | "destructive" | "outline" {
  if (severity === "critical" || severity === "high") return "destructive";
  if (severity === "medium") return "secondary";
  return "outline";
}

export default function RemediationPage() {
  const { selectedRepo } = useRepo();
  const [activeTab, setActiveTab] = useState("api");

  // --- Devin state ---
  const [sessions, setSessions] = useState<DevinSession[]>([]);
  const [devinLoading, setDevinLoading] = useState(false);
  const [devinTriggering, setDevinTriggering] = useState(false);
  const [devinRefreshing, setDevinRefreshing] = useState(false);

  // --- API Remediation state ---
  const [baselineAlerts, setBaselineAlerts] = useState<Alert[]>([]);
  const [selectedAlerts, setSelectedAlerts] = useState<Set<number>>(new Set());
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [remediating, setRemediating] = useState<string | null>(null); // tool key or null
  const [lastResult, setLastResult] = useState<ApiRemediationResponse | null>(null);
  const [apiJobs, setApiJobs] = useState<ApiRemediationJob[]>([]);
  const [costEstimates, setCostEstimates] = useState<ComparisonResult["cost_estimates"]>(null);

  const [error, setError] = useState<string | null>(null);

  // Load baseline open alerts for selection
  const loadBaselineAlerts = useCallback(async () => {
    setAlertsLoading(true);
    setError(null);
    try {
      const data = await api.getLiveAlerts("baseline", "open", selectedRepo);
      setBaselineAlerts(data.alerts);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load alerts";
      if (!msg.includes("404")) {
        setError(msg);
      }
    } finally {
      setAlertsLoading(false);
    }
  }, [selectedRepo]);

  // Load cost estimates for display
  const loadCostEstimates = useCallback(async () => {
    try {
      const data = await api.compareLatest(selectedRepo);
      setCostEstimates(data.cost_estimates);
    } catch {
      // Not critical — cost estimates are optional
    }
  }, [selectedRepo]);

  // Load API remediation job history
  const loadApiJobs = useCallback(async () => {
    try {
      const data = await api.listApiRemediationJobs(undefined, selectedRepo);
      setApiJobs(data);
    } catch {
      // Not critical
    }
  }, [selectedRepo]);

  // Load Devin sessions
  const loadSessions = useCallback(async () => {
    setDevinLoading(true);
    try {
      const data = await api.listDevinSessions(selectedRepo);
      setSessions(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load sessions");
    } finally {
      setDevinLoading(false);
    }
  }, [selectedRepo]);

  useEffect(() => {
    loadBaselineAlerts();
    loadCostEstimates();
    loadApiJobs();
    loadSessions();
  }, [loadBaselineAlerts, loadCostEstimates, loadApiJobs, loadSessions]);

  // Selection handlers
  const toggleAlert = (alertNumber: number) => {
    setSelectedAlerts((prev) => {
      const next = new Set(prev);
      if (next.has(alertNumber)) {
        next.delete(alertNumber);
      } else {
        next.add(alertNumber);
      }
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedAlerts.size === baselineAlerts.length) {
      setSelectedAlerts(new Set());
    } else {
      setSelectedAlerts(new Set(baselineAlerts.map((a) => a.number)));
    }
  };

  // Trigger API remediation
  const triggerApiRemediation = async (toolKey: string) => {
    if (selectedAlerts.size === 0) return;
    setRemediating(toolKey);
    setLastResult(null);
    setError(null);
    try {
      const result = await api.triggerApiRemediation(toolKey, Array.from(selectedAlerts), selectedRepo);
      setLastResult(result);
      await loadApiJobs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Remediation failed");
    } finally {
      setRemediating(null);
    }
  };

  // Devin handlers
  const triggerDevin = async () => {
    setDevinTriggering(true);
    setError(null);
    try {
      await api.triggerDevinRemediation(undefined, undefined, selectedRepo);
      await loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger remediation");
    } finally {
      setDevinTriggering(false);
    }
  };

  const refreshDevin = async () => {
    setDevinRefreshing(true);
    try {
      await api.refreshDevinSessions(selectedRepo);
      await loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh sessions");
    } finally {
      setDevinRefreshing(false);
    }
  };

  const runningSessions = sessions.filter((s) => s.status === "running");
  const completedSessions = sessions.filter((s) => s.status === "completed");
  const failedSessions = sessions.filter((s) => s.status === "failed" || s.status === "stopped");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Remediation</h1>
        <p className="text-muted-foreground mt-1">
          Fix CodeQL alerts using AI tools. Select alerts, choose a tool, and run remediation.
        </p>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="api">API Tools (Anthropic / OpenAI / Google)</TabsTrigger>
          <TabsTrigger value="devin">Devin</TabsTrigger>
        </TabsList>

        {/* ==================== API TOOLS TAB ==================== */}
        <TabsContent value="api" className="space-y-6 mt-4">
          {/* Alert Selection */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Select Alerts to Remediate</CardTitle>
                  <CardDescription>
                    {baselineAlerts.length} open baseline alerts &middot;{" "}
                    {selectedAlerts.size} selected
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={toggleAll}>
                    {selectedAlerts.size === baselineAlerts.length ? "Deselect All" : "Select All"}
                  </Button>
                  <Button variant="outline" size="sm" onClick={loadBaselineAlerts} disabled={alertsLoading}>
                    {alertsLoading ? "Loading..." : "Refresh"}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {alertsLoading && baselineAlerts.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">Loading alerts...</p>
              ) : baselineAlerts.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">
                  No open baseline alerts found. Run a scan first.
                </p>
              ) : (
                <div className="divide-y divide-border max-h-[400px] overflow-y-auto">
                  {baselineAlerts.map((alert) => (
                    <label
                      key={alert.number}
                      className="flex items-start gap-3 py-2.5 px-2 -mx-2 rounded-md hover:bg-muted/50 cursor-pointer transition-colors"
                    >
                      <input
                        type="checkbox"
                        checked={selectedAlerts.has(alert.number)}
                        onChange={() => toggleAlert(alert.number)}
                        className="mt-1 h-4 w-4 rounded border-border"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="font-medium text-sm tabular-nums">#{alert.number}</span>
                          <Badge variant={getSeverityVariant(alert.severity)}>
                            {alert.severity}
                          </Badge>
                        </div>
                        <p className="text-sm truncate">{alert.rule_description || alert.rule_id}</p>
                        <p className="text-xs text-muted-foreground truncate">
                          {alert.file_path}:{alert.start_line}
                        </p>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Tool Action Buttons */}
          <Card>
            <CardHeader>
              <CardTitle>Run Remediation</CardTitle>
              <CardDescription>
                Choose a tool to fix the {selectedAlerts.size} selected alert{selectedAlerts.size !== 1 ? "s" : ""}.
                Each alert will be processed sequentially — the LLM generates a fix and commits it to the tool&apos;s branch.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-3">
                {API_TOOLS.map((tool) => {
                  const cost = costEstimates?.[tool.key];
                  const isRunning = remediating === tool.key;

                  return (
                    <div key={tool.key} className="relative border rounded-lg p-4 space-y-3">
                      <div className={`absolute top-0 left-0 right-0 h-1 rounded-t-lg ${tool.color}`} />
                      <div>
                        <p className="font-semibold">{tool.label}</p>
                        <p className="text-xs text-muted-foreground">{tool.model}</p>
                      </div>
                      {cost && (
                        <p className="text-xs text-muted-foreground">
                          Est. cost: ${cost.total_cost_usd.toFixed(4)} &middot;{" "}
                          {cost.estimated_input_tokens.toLocaleString()} tokens
                        </p>
                      )}
                      <Button
                        className="w-full"
                        onClick={() => triggerApiRemediation(tool.key)}
                        disabled={selectedAlerts.size === 0 || remediating !== null}
                      >
                        {isRunning ? "Remediating..." : `Remediate with ${tool.label}`}
                      </Button>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          {/* Last Result */}
          {lastResult && (
            <Card>
              <CardHeader>
                <CardTitle>Remediation Result</CardTitle>
                <CardDescription>{lastResult.message}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-3 gap-4 mb-4">
                  <div>
                    <p className="text-2xl font-bold text-emerald-500">{lastResult.completed}</p>
                    <p className="text-xs text-muted-foreground">Completed</p>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-destructive">{lastResult.failed}</p>
                    <p className="text-xs text-muted-foreground">Failed</p>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-muted-foreground">{lastResult.skipped}</p>
                    <p className="text-xs text-muted-foreground">Skipped</p>
                  </div>
                </div>
                {lastResult.jobs.length > 0 && (
                  <div className="divide-y divide-border">
                    {lastResult.jobs.map((job) => (
                      <div key={job.id} className="flex items-start gap-3 py-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="font-medium text-sm">Alert #{job.alert_number}</span>
                            <Badge variant={STATUS_VARIANTS[job.status] || "outline"}>
                              {job.status}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground truncate">{job.file_path}</p>
                          {job.error_message && (
                            <p className="text-xs text-destructive mt-0.5 truncate">{job.error_message}</p>
                          )}
                        </div>
                        {job.commit_sha && (
                          <span className="text-xs text-muted-foreground font-mono">
                            {job.commit_sha.slice(0, 8)}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Job History */}
          {apiJobs.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Job History</CardTitle>
                <CardDescription>{apiJobs.length} total API remediation jobs</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="divide-y divide-border max-h-[300px] overflow-y-auto">
                  {apiJobs.map((job) => (
                    <div key={job.id} className="flex items-start gap-3 py-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="font-medium text-sm">Alert #{job.alert_number}</span>
                          <Badge variant="outline">{job.tool}</Badge>
                          <Badge variant={STATUS_VARIANTS[job.status] || "outline"}>
                            {job.status}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground truncate">{job.file_path}</p>
                        {job.error_message && (
                          <p className="text-xs text-destructive mt-0.5 truncate">{job.error_message}</p>
                        )}
                      </div>
                      <div className="text-right shrink-0">
                        <p className="text-xs text-muted-foreground">
                          {new Date(job.created_at).toLocaleDateString()}
                        </p>
                        {job.commit_sha && (
                          <span className="text-xs text-muted-foreground font-mono">
                            {job.commit_sha.slice(0, 8)}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ==================== DEVIN TAB ==================== */}
        <TabsContent value="devin" className="space-y-6 mt-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Devin Remediation</h2>
              <p className="text-sm text-muted-foreground">
                Manage automated Devin sessions for fixing CodeQL alerts
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Button onClick={triggerDevin} disabled={devinTriggering}>
                {devinTriggering ? "Creating Sessions..." : "Launch Remediation"}
              </Button>
              <Button variant="outline" onClick={refreshDevin} disabled={devinRefreshing}>
                {devinRefreshing ? "Refreshing..." : "Refresh Status"}
              </Button>
            </div>
          </div>

          {/* Summary Stats */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Running</CardDescription>
                <CardTitle className="text-3xl">{runningSessions.length}</CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Completed</CardDescription>
                <CardTitle className="text-3xl text-emerald-500">{completedSessions.length}</CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Failed / Stopped</CardDescription>
                <CardTitle className="text-3xl text-destructive">{failedSessions.length}</CardTitle>
              </CardHeader>
            </Card>
          </div>

          {/* Session List */}
          <Card>
            <CardHeader>
              <CardTitle>Session History</CardTitle>
              <CardDescription>
                {sessions.length} total sessions
                {devinLoading && " · Loading..."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {sessions.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">
                  No remediation sessions yet. Click &ldquo;Launch Remediation&rdquo; to start fixing alerts with Devin.
                </p>
              ) : (
                <div className="divide-y divide-border">
                  {sessions.map((session) => (
                    <div key={session.id} className="flex items-start gap-4 py-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-sm">Alert #{session.alert_number}</span>
                          <Badge variant={STATUS_VARIANTS[session.status] || "outline"}>
                            {session.status}
                          </Badge>
                        </div>
                        <p className="text-sm text-muted-foreground truncate">{session.rule_id}</p>
                        <p className="text-xs text-muted-foreground truncate">{session.file_path}</p>
                        {session.pr_url && (
                          <a
                            href={session.pr_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-primary hover:underline mt-1 inline-block"
                          >
                            View PR
                          </a>
                        )}
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-muted-foreground">
                          {new Date(session.created_at).toLocaleDateString()}
                        </p>
                        <a
                          href={`https://app.devin.ai/sessions/${session.session_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-primary hover:underline"
                        >
                          Devin Session
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
