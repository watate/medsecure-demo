"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type DevinSession } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  running: "default",
  completed: "secondary",
  failed: "destructive",
  blocked: "outline",
  stopped: "outline",
};

export default function RemediationPage() {
  const [sessions, setSessions] = useState<DevinSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listDevinSessions();
      setSessions(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerRemediation = async () => {
    setTriggering(true);
    setError(null);
    try {
      await api.triggerDevinRemediation();
      await loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger remediation");
    } finally {
      setTriggering(false);
    }
  };

  const refreshStatuses = async () => {
    setRefreshing(true);
    try {
      await api.refreshDevinSessions();
      await loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh sessions");
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const runningSessions = sessions.filter((s) => s.status === "running");
  const completedSessions = sessions.filter((s) => s.status === "completed");
  const failedSessions = sessions.filter((s) => s.status === "failed" || s.status === "stopped");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Devin Remediation</h1>
          <p className="text-muted-foreground mt-1">
            Manage automated Devin sessions for fixing CodeQL alerts
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={triggerRemediation} disabled={triggering}>
            {triggering ? "Creating Sessions..." : "Launch Remediation"}
          </Button>
          <Button variant="outline" onClick={refreshStatuses} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh Status"}
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
          <CardDescription>{sessions.length} total sessions</CardDescription>
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
    </div>
  );
}
