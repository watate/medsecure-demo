"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api, type Alert, type AlertsResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const TOOLS = [
  { key: "baseline", label: "Baseline" },
  { key: "devin", label: "Devin" },
  { key: "copilot", label: "Copilot" },
  { key: "anthropic", label: "Anthropic" },
  { key: "openai", label: "OpenAI" },
];

const STATE_OPTIONS = [
  { value: "open", label: "Open" },
  { value: "fixed", label: "Fixed" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];

const SEVERITY_OPTIONS = [
  { value: "all", label: "All Severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const SORT_OPTIONS = [
  { value: "number-desc", label: "Number (newest)" },
  { value: "number-asc", label: "Number (oldest)" },
  { value: "severity", label: "Severity" },
  { value: "file", label: "File path" },
];

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  note: 4,
  warning: 5,
};

function getSeverityVariant(severity: string): "default" | "secondary" | "destructive" | "outline" {
  if (severity === "critical" || severity === "high") return "destructive";
  if (severity === "medium") return "secondary";
  return "outline";
}

function AlertSkeleton() {
  return (
    <div className="space-y-3 py-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-start gap-4 py-3 px-2">
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <Skeleton className="h-4 w-10 rounded-md" />
              <Skeleton className="h-5 w-16 rounded-md" />
              <Skeleton className="h-5 w-12 rounded-md" />
            </div>
            <Skeleton className="h-4 w-3/4 rounded-md" />
            <Skeleton className="h-3 w-1/2 rounded-md" />
          </div>
          <Skeleton className="h-3 w-20 rounded-md" />
        </div>
      ))}
    </div>
  );
}

// Cache type: keyed by "tool:state"
type AlertsCache = Record<string, AlertsResponse>;

export default function AlertsPage() {
  const [selectedTool, setSelectedTool] = useState("baseline");
  const [stateFilter, setStateFilter] = useState("open");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [sortBy, setSortBy] = useState("number-desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cacheRef = useRef<AlertsCache>({});
  const requestIdRef = useRef(0);
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);

  const cacheKey = `${selectedTool}:${stateFilter}`;

  // Shared fetch function guarded by requestIdRef to prevent race conditions.
  // Both useEffect and forceRefresh increment the ID before firing; callbacks
  // only update state if their captured ID still matches the current ref value.
  const fetchAlerts = useCallback((tool: string, state: string, key: string) => {
    const id = ++requestIdRef.current;
    setLoading(true);
    setError(null);

    const apiState = state === "all" ? undefined : state;
    api.getLiveAlerts(tool, apiState)
      .then((data) => {
        cacheRef.current[key] = data;
        if (requestIdRef.current === id) setAlerts(data);
      })
      .catch((e) => {
        if (requestIdRef.current === id) {
          const msg = e instanceof Error ? e.message : "Failed to load alerts";
          if (!cacheRef.current[key]) {
            setError(msg);
          }
        }
      })
      .finally(() => {
        if (requestIdRef.current === id) setLoading(false);
      });
  }, []);

  // Re-fetch when tool or state changes
  useEffect(() => {
    const cached = cacheRef.current[cacheKey];
    if (cached) {
      setAlerts(cached);
    } else {
      setAlerts(null);
    }
    fetchAlerts(selectedTool, stateFilter, cacheKey);
  }, [selectedTool, stateFilter, cacheKey, fetchAlerts]);

  const forceRefresh = useCallback(() => {
    delete cacheRef.current[cacheKey];
    setAlerts(null);
    fetchAlerts(selectedTool, stateFilter, cacheKey);
  }, [selectedTool, stateFilter, cacheKey, fetchAlerts]);

  // Filter and sort the alerts client-side
  const filteredAlerts = alerts?.alerts
    ?.filter((a: Alert) => severityFilter === "all" || a.severity?.toLowerCase() === severityFilter)
    ?.sort((a: Alert, b: Alert) => {
      switch (sortBy) {
        case "number-asc":
          return a.number - b.number;
        case "number-desc":
          return b.number - a.number;
        case "severity":
          return (SEVERITY_ORDER[a.severity?.toLowerCase()] ?? 99) - (SEVERITY_ORDER[b.severity?.toLowerCase()] ?? 99);
        case "file":
          return (a.file_path || "").localeCompare(b.file_path || "");
        default:
          return 0;
      }
    }) ?? [];

  const showSkeleton = loading && !alerts;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">CodeQL Alerts</h1>
        <p className="text-muted-foreground mt-1">
          Browse alerts by tool branch. Click an alert to view on GitHub.
        </p>
      </div>

      {/* Tool Tabs */}
      <Tabs value={selectedTool} onValueChange={setSelectedTool}>
        <TabsList>
          {TOOLS.map((tool) => (
            <TabsTrigger key={tool.key} value={tool.key}>
              {tool.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Filters Row */}
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={stateFilter} onValueChange={setStateFilter}>
          <TabsList>
            {STATE_OPTIONS.map((opt) => (
              <TabsTrigger key={opt.value} value={opt.value}>
                {opt.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <Select value={severityFilter} onValueChange={setSeverityFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SEVERITY_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sortBy} onValueChange={setSortBy}>
          <SelectTrigger className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORT_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button
          variant="outline"
          size="sm"
          onClick={forceRefresh}
          disabled={loading}
        >
          {loading ? "Loading..." : "Refresh"}
        </Button>

        {loading && alerts && (
          <span className="text-xs text-muted-foreground animate-pulse">Updating...</span>
        )}
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

      {showSkeleton && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <Skeleton className="h-5 w-48 rounded-md" />
              <Skeleton className="h-5 w-20 rounded-md" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AlertSkeleton />
          </CardContent>
        </Card>
      )}

      {alerts && !showSkeleton && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>
                {TOOLS.find((t) => t.key === alerts.tool)?.label || alerts.tool} &mdash; {alerts.branch}
              </span>
              <div className="flex items-center gap-2">
                <Badge variant="secondary">
                  {filteredAlerts.length}{filteredAlerts.length !== alerts.total ? ` / ${alerts.total}` : ""} alerts
                </Badge>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {filteredAlerts.length === 0 ? (
              <p className="text-center text-muted-foreground py-8">
                {severityFilter !== "all"
                  ? `No ${severityFilter} alerts found`
                  : "No alerts found"}
              </p>
            ) : (
              <div className="divide-y divide-border">
                {filteredAlerts.map((alert: Alert) => (
                  <a
                    key={alert.number}
                    href={`${alert.html_url}?ref=refs/heads/${alerts.branch}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-start gap-4 py-3 px-2 -mx-2 rounded-md hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm tabular-nums">#{alert.number}</span>
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
