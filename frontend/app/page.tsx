"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type ComparisonResult } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const TOOL_LABELS: Record<string, string> = {
  devin: "Devin",
  copilot: "Copilot Autofix",
  anthropic: "Anthropic (claude-opus-4-6)",
  openai: "OpenAI (gpt-5.3-codex)",
};

const TOOL_COLORS: Record<string, string> = {
  devin: "bg-emerald-500",
  copilot: "bg-blue-500",
  anthropic: "bg-orange-500",
  openai: "bg-violet-500",
};

function SeverityBar({ label, count, total, color }: { label: string; count: number; total: number; color: string }) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-16 text-xs text-muted-foreground capitalize">{label}</span>
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-xs text-right font-medium">{count}</span>
    </div>
  );
}

export default function DashboardPage() {
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadComparison = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.compareLatest();
      setComparison(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load comparison";
      // 404 means no scans yet — show empty state, not an error
      if (msg.includes("404")) {
        setComparison(null);
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await api.triggerScan();
      await loadComparison();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger scan");
    } finally {
      setScanning(false);
    }
  };

  useEffect(() => {
    loadComparison();
  }, [loadComparison]);

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Security Remediation Dashboard</h1>
          <p className="text-muted-foreground mt-1">
            Compare CodeQL remediation performance across AI tools
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={triggerScan} disabled={scanning}>
            {scanning ? "Scanning..." : "Run New Scan"}
          </Button>
          <Button variant="outline" onClick={loadComparison} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
            {error.includes("403") && (
              <p className="text-muted-foreground text-xs mt-2">
                Your GITHUB_TOKEN likely needs the &apos;security_events&apos; scope (classic PAT)
                or &apos;Code scanning alerts: Read&apos; permission (fine-grained PAT).
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {comparison && (
        <>
          {/* Baseline Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                Baseline
                <Badge variant="secondary">{comparison.baseline.branch}</Badge>
              </CardTitle>
              <CardDescription>
                {comparison.repo} &middot; Scanned {new Date(comparison.scanned_at).toLocaleString()}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-5">
                <div>
                  <p className="text-3xl font-bold">{comparison.baseline.open}</p>
                  <p className="text-xs text-muted-foreground">Open Alerts</p>
                </div>
                <div>
                  <p className="text-3xl font-bold text-red-500">{comparison.baseline.critical}</p>
                  <p className="text-xs text-muted-foreground">Critical</p>
                </div>
                <div>
                  <p className="text-3xl font-bold text-orange-500">{comparison.baseline.high}</p>
                  <p className="text-xs text-muted-foreground">High</p>
                </div>
                <div>
                  <p className="text-3xl font-bold text-yellow-500">{comparison.baseline.medium}</p>
                  <p className="text-xs text-muted-foreground">Medium</p>
                </div>
                <div>
                  <p className="text-3xl font-bold text-blue-500">{comparison.baseline.low}</p>
                  <p className="text-xs text-muted-foreground">Low</p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Tool Comparison Cards */}
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
            {Object.entries(comparison.tools).map(([toolName, summary]) => {
              const improvement = comparison.improvements[toolName];
              const fixRate = improvement?.fix_rate_pct ?? 0;

              return (
                <Card key={toolName} className="relative overflow-hidden">
                  <div className={`absolute top-0 left-0 right-0 h-1 ${TOOL_COLORS[toolName] || "bg-gray-500"}`} />
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                      {TOOL_LABELS[toolName] || toolName}
                      <Badge variant={fixRate > 50 ? "default" : "secondary"}>
                        {fixRate}% fixed
                      </Badge>
                    </CardTitle>
                    <CardDescription>{summary.branch}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <p className="text-2xl font-bold">{summary.open}</p>
                        <p className="text-xs text-muted-foreground">Remaining</p>
                      </div>
                      <div>
                        <p className="text-2xl font-bold text-emerald-500">
                          {improvement?.total_fixed ?? 0}
                        </p>
                        <p className="text-xs text-muted-foreground">Fixed</p>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <SeverityBar label="Critical" count={summary.critical} total={comparison.baseline.critical || 1} color="bg-red-500" />
                      <SeverityBar label="High" count={summary.high} total={comparison.baseline.high || 1} color="bg-orange-500" />
                      <SeverityBar label="Medium" count={summary.medium} total={comparison.baseline.medium || 1} color="bg-yellow-500" />
                      <SeverityBar label="Low" count={summary.low} total={comparison.baseline.low || 1} color="bg-blue-500" />
                    </div>

                    {/* Cost estimate for API-based tools */}
                    {comparison.cost_estimates?.[toolName] && (
                      <div className="pt-3 border-t">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">Est. remediation cost</span>
                          <span className="text-sm font-semibold">
                            ${comparison.cost_estimates[toolName].total_cost_usd.toFixed(4)}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          {comparison.cost_estimates[toolName].model} &middot;{" "}
                          {comparison.cost_estimates[toolName].estimated_input_tokens.toLocaleString()} tokens
                        </p>
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </>
      )}

      {!comparison && !loading && !error && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <p className="text-lg font-medium text-muted-foreground">No scan data yet</p>
            <p className="text-sm text-muted-foreground mt-1">
              Click &ldquo;Run New Scan&rdquo; to fetch CodeQL alerts and start comparing tools
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
