"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type Alert, type AlertsResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const TOOLS = [
  { key: "baseline", label: "Baseline" },
  { key: "devin", label: "Devin" },
  { key: "copilot", label: "Copilot Autofix" },
  { key: "anthropic", label: "Anthropic" },
];

const SEVERITY_COLORS: Record<string, string> = {
  critical: "destructive",
  high: "destructive",
  medium: "secondary",
  low: "secondary",
};

function getSeverityVariant(severity: string): "default" | "secondary" | "destructive" | "outline" {
  if (severity === "critical" || severity === "high") return "destructive";
  if (severity === "medium") return "secondary";
  return "outline";
}

export default function AlertsPage() {
  const [selectedTool, setSelectedTool] = useState("baseline");
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stateFilter, setStateFilter] = useState<string>("open");

  const loadAlerts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getLiveAlerts(selectedTool, stateFilter || undefined);
      setAlerts(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [selectedTool, stateFilter]);

  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">CodeQL Alerts</h1>
        <p className="text-muted-foreground mt-1">
          Browse alerts by tool branch. Click an alert to view on GitHub.
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-lg border border-border p-1">
          {TOOLS.map((tool) => (
            <button
              key={tool.key}
              onClick={() => setSelectedTool(tool.key)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                selectedTool === tool.key
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {tool.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 rounded-lg border border-border p-1">
          {["open", "fixed", "dismissed", ""].map((state) => (
            <button
              key={state || "all"}
              onClick={() => setStateFilter(state)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                stateFilter === state
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {state || "All"}
            </button>
          ))}
        </div>

        <Button variant="outline" size="sm" onClick={loadAlerts} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </Button>
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

      {alerts && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>
                {alerts.tool} &mdash; {alerts.branch}
              </span>
              <Badge variant="secondary">{alerts.total} alerts</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {alerts.alerts.length === 0 ? (
              <p className="text-center text-muted-foreground py-8">No alerts found</p>
            ) : (
              <div className="divide-y divide-border">
                {alerts.alerts.map((alert: Alert) => (
                  <a
                    key={alert.number}
                    href={alert.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-start gap-4 py-3 px-2 -mx-2 rounded-md hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm">#{alert.number}</span>
                        <Badge variant={getSeverityVariant(alert.severity)}>
                          {alert.severity}
                        </Badge>
                        <Badge variant="outline">{alert.state}</Badge>
                      </div>
                      <p className="text-sm truncate">{alert.rule_description || alert.rule_id}</p>
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {alert.file_path}:{alert.start_line}
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(alert.created_at).toLocaleDateString()}
                    </span>
                  </a>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
