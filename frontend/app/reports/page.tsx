"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type ReportData } from "@/lib/api";
import { useRepo } from "@/lib/repo-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TOOL_LABELS: Record<string, string> = {
  devin: "Devin",
  copilot: "Copilot Autofix",
  anthropic: "Anthropic (claude-opus-4-6)",
  openai: "OpenAI (gpt-5.3-codex)",
  gemini: "Google (gemini-3.1-pro-preview)",
};

function SeverityDot({ color }: { color: string }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />;
}

function CISOReport({ data }: { data: ReportData }) {
  const headline = data.headline || {};
  const toolPerf = data.tool_performance || {};
  const severityBA = data.severity_before_after || {};
  const verification = data.verification || {};

  return (
    <div className="space-y-6">
      {/* Headline Numbers */}
      <Card>
        <CardHeader>
          <CardTitle>Security Remediation Summary</CardTitle>
          <CardDescription>
            {data.repo} &middot; Scan date: {data.scan_date ? new Date(data.scan_date).toLocaleDateString() : "N/A"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-5">
            <div>
              <p className="text-3xl font-bold">{headline.baseline_open ?? 0}</p>
              <p className="text-xs text-muted-foreground">Open Alerts</p>
            </div>
            <div>
              <p className="text-3xl font-bold text-red-500">{headline.baseline_critical ?? 0}</p>
              <p className="text-xs text-muted-foreground">Critical</p>
            </div>
            <div>
              <p className="text-3xl font-bold text-emerald-500">{headline.best_fix_rate_pct ?? 0}%</p>
              <p className="text-xs text-muted-foreground">
                Best Fix Rate ({headline.best_tool ? TOOL_LABELS[headline.best_tool] || headline.best_tool : "N/A"})
              </p>
            </div>
            <div>
              <p className="text-3xl font-bold">{headline.best_time ?? "N/A"}</p>
              <p className="text-xs text-muted-foreground">Time to Remediate</p>
            </div>
            <div>
              <p className={`text-3xl font-bold ${headline.best_regressions === 0 ? "text-emerald-500" : "text-red-500"}`}>
                {headline.best_regressions ?? 0}
              </p>
              <p className="text-xs text-muted-foreground">Regressions</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tool Performance — Speed, Coverage, Correctness */}
      <Card>
        <CardHeader>
          <CardTitle>Tool Performance Comparison</CardTitle>
          <CardDescription>Speed, coverage, and correctness side-by-side</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="pb-2 font-medium">Metric</th>
                  {Object.keys(toolPerf).map((tool) => (
                    <th key={tool} className="pb-2 font-medium">{TOOL_LABELS[tool] || tool}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                <tr>
                  <td className="py-2 text-muted-foreground">Alerts Fixed</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold text-emerald-600">
                      {(info as Record<string, number>).total_fixed}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Fix Rate</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold">
                      {(info as Record<string, number>).fix_rate_pct}%
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Critical Fixed</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold">
                      {(info as Record<string, number>).critical_fixed}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Regressions Introduced</td>
                  {Object.entries(toolPerf).map(([tool, info]) => {
                    const count = (info as Record<string, number>).regressions_introduced;
                    return (
                      <td key={tool} className={`py-2 font-semibold ${count > 0 ? "text-red-500" : "text-emerald-600"}`}>
                        {count}
                      </td>
                    );
                  })}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Remediation Time</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold">
                      {(info as Record<string, string>).total_time || "N/A"}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Avg Time per Fix</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2">
                      {(info as Record<string, string>).avg_time_per_fix || "N/A"}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Automation Level</td>
                  {Object.entries(toolPerf).map(([tool, info]) => (
                    <td key={tool} className="py-2 text-xs">
                      {(info as Record<string, string>).automation_level}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Severity Before/After */}
      <Card>
        <CardHeader>
          <CardTitle>Severity Breakdown &mdash; Before vs After</CardTitle>
          <CardDescription>How each tool reduced alerts by severity level</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-5">
            {Object.entries(severityBA).map(([tool, severities]) => {
              const s = severities as Record<string, Record<string, number>>;
              return (
                <div key={tool} className="rounded-lg border p-4 space-y-3">
                  <p className="font-medium">{TOOL_LABELS[tool] || tool}</p>
                  {(["critical", "high", "medium", "low"] as const).map((sev) => {
                    const info = s[sev] || { before: 0, after: 0, fixed: 0 };
                    const pct = info.before > 0 ? Math.round((info.fixed / info.before) * 100) : 0;
                    const barColor = sev === "critical" ? "bg-red-500" : sev === "high" ? "bg-orange-500" : sev === "medium" ? "bg-yellow-500" : "bg-blue-500";
                    return (
                      <div key={sev} className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="capitalize font-medium">{sev}</span>
                          <span className="text-muted-foreground">
                            {info.before} &rarr; {info.after} ({info.fixed} fixed)
                          </span>
                        </div>
                        <div className="h-2 bg-muted rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Cost Estimates */}
      {Object.entries(toolPerf).some(([, info]) => (info as Record<string, unknown>).cost_estimate) && (
        <Card>
          <CardHeader>
            <CardTitle>API Cost Estimates</CardTitle>
            <CardDescription>Estimated token usage and cost per tool</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 md:grid-cols-2">
              {Object.entries(toolPerf)
                .filter(([, info]) => (info as Record<string, unknown>).cost_estimate)
                .map(([tool, info]) => {
                  const cost = (info as Record<string, Record<string, number | string | Record<string, number>>>).cost_estimate;
                  return (
                    <div key={tool} className="rounded-lg border p-4 space-y-2">
                      <p className="font-medium">{TOOL_LABELS[tool] || tool}</p>
                      <p className="text-xs text-muted-foreground">Model: {cost.model as string}</p>
                      <div className="space-y-1">
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Input tokens</span>
                          <span className="font-mono">{(cost.estimated_input_tokens as number).toLocaleString()}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Output tokens</span>
                          <span className="font-mono">{(cost.estimated_output_tokens as number).toLocaleString()}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Input cost</span>
                          <span className="font-semibold">${(cost.input_cost_usd as number).toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Output cost</span>
                          <span className="font-semibold">${(cost.output_cost_usd as number).toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between text-sm border-t pt-1">
                          <span className="font-medium">Total</span>
                          <span className="font-bold text-emerald-600">${(cost.total_cost_usd as number).toFixed(4)}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Verification & Correctness */}
      <Card className="border-emerald-500/30">
        <CardHeader>
          <CardTitle>Fix Verification</CardTitle>
          <CardDescription>{verification.method}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">{verification.description}</p>
          <div className="rounded-lg bg-muted/50 p-4 space-y-2">
            <p className="text-sm font-medium">Why Devin is different:</p>
            <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside">
              <li>Runs build and test suites in a sandboxed environment before pushing fixes</li>
              <li>Iterates on failures automatically, no human intervention needed</li>
              <li>Other coding agents generate patches without testing them</li>
            </ul>
          </div>
        </CardContent>
      </Card>

    </div>
  );
}

function CTOReport({ data }: { data: ReportData }) {
  const exec = data.executive_summary || {};
  const toolComparison = data.tool_comparison || {};
  const roiAnalysis = data.roi_analysis || {};
  const backlog = data.backlog_impact || {};
  const workflow = data.integration_workflow || {};
  const recommendation = data.recommendation || {};
  const priceComparison = data.price_comparison || {};

  return (
    <div className="space-y-6">
      {/* Executive Summary */}
      <Card>
        <CardHeader>
          <CardTitle>Executive Summary</CardTitle>
          <CardDescription>
            {data.repo} &middot; {data.scan_date ? new Date(data.scan_date).toLocaleDateString() : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            <div>
              <p className="text-3xl font-bold">{exec.baseline_open_alerts ?? 0}</p>
              <p className="text-xs text-muted-foreground">Open Alerts</p>
            </div>
            <div>
              <p className="text-3xl font-bold text-emerald-500">{exec.best_fix_rate_pct ?? 0}%</p>
              <p className="text-xs text-muted-foreground">
                Best Fix Rate ({exec.best_tool ? TOOL_LABELS[exec.best_tool] || exec.best_tool : "N/A"})
              </p>
            </div>
            <div>
              <p className="text-3xl font-bold">{exec.total_tools_compared ?? 0}</p>
              <p className="text-xs text-muted-foreground">Tools Compared</p>
            </div>
            <div>
              <SeverityDot color="bg-emerald-500" />
              <p className="text-xs text-muted-foreground mt-1">Automated remediation</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tool Comparison Matrix */}
      <Card>
        <CardHeader>
          <CardTitle>Tool Comparison Matrix</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="pb-2 font-medium">Metric</th>
                  {Object.keys(toolComparison).map((tool) => (
                    <th key={tool} className="pb-2 font-medium">{TOOL_LABELS[tool] || tool}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                <tr>
                  <td className="py-2 text-muted-foreground">Alerts Fixed</td>
                  {Object.entries(toolComparison).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold">
                      {(info as Record<string, number>).total_fixed}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Fix Rate</td>
                  {Object.entries(toolComparison).map(([tool, info]) => (
                    <td key={tool} className="py-2 font-semibold">
                      {(info as Record<string, number>).fix_rate_pct}%
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">New Alerts Introduced</td>
                  {Object.entries(toolComparison).map(([tool, info]) => {
                    const count = (info as Record<string, number>).new_alerts_introduced;
                    return (
                      <td key={tool} className={`py-2 font-semibold ${count > 0 ? "text-red-500" : "text-emerald-600"}`}>
                        {count}
                      </td>
                    );
                  })}
                </tr>
                <tr>
                  <td className="py-2 text-muted-foreground">Human Intervention</td>
                  {Object.entries(toolComparison).map(([tool, info]) => (
                    <td key={tool} className="py-2 text-xs">
                      {(info as Record<string, string>).human_intervention}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* ROI Analysis */}
      <Card>
        <CardHeader>
          <CardTitle>ROI Analysis</CardTitle>
          <CardDescription>
            Assumptions: ${roiAnalysis.assumptions?.avg_engineer_hourly_cost_usd}/hr eng cost,{" "}
            {roiAnalysis.assumptions?.avg_manual_fix_minutes} min avg manual fix time
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
            {Object.entries(roiAnalysis.tools || {}).map(([tool, info]) => {
              const r = info as Record<string, number>;
              return (
                <div key={tool} className="rounded-lg border p-4 space-y-2">
                  <p className="font-medium">{TOOL_LABELS[tool] || tool}</p>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Developer Hours Saved</span>
                      <span className="font-semibold">{r.developer_hours_saved}hr</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Manual Cost Avoided</span>
                      <span className="font-semibold text-emerald-600">${r.manual_cost_usd?.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Alerts Fixed</span>
                      <span className="font-semibold">{r.alerts_fixed}</span>
                    </div>
                    {r.tool_cost_usd > 0 && (
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Tool Cost</span>
                        <span className="font-semibold text-amber-600">${r.tool_cost_usd?.toFixed(4)}</span>
                      </div>
                    )}
                    {r.net_savings_usd > 0 && (
                      <div className="flex justify-between text-sm border-t pt-1">
                        <span className="font-medium">Net Savings</span>
                        <span className="font-bold text-emerald-600">${r.net_savings_usd?.toLocaleString()}</span>
                      </div>
                    )}
                    {r.roi_pct > 0 && (
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">ROI</span>
                        <span className="font-semibold text-emerald-600">{r.roi_pct}%</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Price Comparison */}
      {priceComparison.tools && Object.keys(priceComparison.tools).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Price Comparison</CardTitle>
            <CardDescription>
              Side-by-side cost comparison across all remediation tools
              {priceComparison.cheapest_tool && (
                <> &middot; Cheapest: <span className="font-semibold text-emerald-600">{TOOL_LABELS[priceComparison.cheapest_tool] || priceComparison.cheapest_tool}</span></>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 font-medium">Tool</th>
                    <th className="pb-2 font-medium text-right">Pricing Type</th>
                    <th className="pb-2 font-medium text-right">Alerts Fixed</th>
                    <th className="pb-2 font-medium text-right">Total Cost</th>
                    <th className="pb-2 font-medium text-right">Cost / Fix</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(priceComparison.ranked_by_cost_per_fix || []).map((toolKey: string, idx: number) => {
                    const t = priceComparison.tools[toolKey] as Record<string, string | number>;
                    if (!t) return null;
                    const isCheapest = idx === 0;
                    return (
                      <tr key={toolKey} className={isCheapest ? "bg-emerald-50 dark:bg-emerald-950/20" : ""}>
                        <td className="py-2 font-medium">
                          {t.display_name as string}
                          {isCheapest && <span className="ml-2 text-xs text-emerald-600 font-semibold">BEST VALUE</span>}
                        </td>
                        <td className="py-2 text-right text-muted-foreground capitalize">
                          {(t.pricing_type as string)?.replace(/_/g, " ")}
                        </td>
                        <td className="py-2 text-right font-semibold">{t.alerts_fixed as number}</td>
                        <td className="py-2 text-right font-mono font-semibold">
                          ${(t.total_cost_usd as number)?.toFixed(4)}
                        </td>
                        <td className="py-2 text-right font-mono font-bold">
                          ${(t.cost_per_fix_usd as number)?.toFixed(4)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Security Backlog Impact */}
      <Card>
        <CardHeader>
          <CardTitle>Security Backlog Impact</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
            {Object.entries(backlog).map(([tool, info]) => {
              const b = info as Record<string, number>;
              return (
                <div key={tool} className="rounded-lg border p-4 space-y-2">
                  <p className="font-medium">{TOOL_LABELS[tool] || tool}</p>
                  <div className="flex items-end gap-2">
                    <span className="text-2xl font-bold">{b.before}</span>
                    <span className="text-muted-foreground mb-1">&#8594;</span>
                    <span className="text-2xl font-bold text-emerald-600">{b.after}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {b.reduction_pct}% backlog reduction
                  </p>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 rounded-full"
                      style={{ width: `${b.reduction_pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Cost Estimates */}
      {Object.entries(toolComparison).some(([, info]) => (info as Record<string, unknown>).cost_estimate) && (
        <Card>
          <CardHeader>
            <CardTitle>API Cost Estimates</CardTitle>
            <CardDescription>Estimated token usage and cost per tool</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 md:grid-cols-2">
              {Object.entries(toolComparison)
                .filter(([, info]) => (info as Record<string, unknown>).cost_estimate)
                .map(([tool, info]) => {
                  const cost = (info as Record<string, Record<string, number | string | Record<string, number>>>).cost_estimate;
                  return (
                    <div key={tool} className="rounded-lg border p-4 space-y-2">
                      <p className="font-medium">{TOOL_LABELS[tool] || tool}</p>
                      <p className="text-xs text-muted-foreground">Model: {cost.model as string}</p>
                      <div className="space-y-1">
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Input tokens</span>
                          <span className="font-mono">{(cost.estimated_input_tokens as number).toLocaleString()}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Output tokens</span>
                          <span className="font-mono">{(cost.estimated_output_tokens as number).toLocaleString()}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Input cost</span>
                          <span className="font-semibold">${(cost.input_cost_usd as number).toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">Output cost</span>
                          <span className="font-semibold">${(cost.output_cost_usd as number).toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between text-sm border-t pt-1">
                          <span className="font-medium">Total</span>
                          <span className="font-bold text-emerald-600">${(cost.total_cost_usd as number).toFixed(4)}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Integration Workflow */}
      <Card>
        <CardHeader>
          <CardTitle>Automated Remediation Pipeline</CardTitle>
          <CardDescription>{workflow.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="space-y-2">
            {(workflow.steps || []).map((step: string, i: number) => (
              <li key={i} className="flex items-start gap-3 text-sm">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                  {i + 1}
                </span>
                <span className="mt-0.5">{step}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      {/* Recommendation */}
      {recommendation.tool && (
        <Card className="border-emerald-500/50">
          <CardHeader>
            <CardTitle className="text-emerald-600">Recommendation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="font-medium">{recommendation.summary}</p>
            <p className="text-sm text-muted-foreground">{recommendation.details}</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function ReportsPage() {
  const { selectedRepo } = useRepo();
  const [reportType, setReportType] = useState<"ciso" | "cto">("ciso");
  const [report, setReport] = useState<ReportData | null>(null);
  const [generating, setGenerating] = useState(false);
  const [loadingLatest, setLoadingLatest] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generateReport = async () => {
    setGenerating(true);
    setError(null);
    try {
      const data = await api.generateReport(reportType, undefined, undefined, undefined, selectedRepo);
      setReport(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to generate report";
      setError(msg);
    } finally {
      setGenerating(false);
    }
  };

  const loadLatest = async () => {
    setLoadingLatest(true);
    setError(null);
    try {
      const data = await api.getLatestReport(reportType, selectedRepo);
      setReport(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "No report found";
      if (msg.includes("404")) {
        setReport(null);
      } else {
        setError(msg);
      }
    } finally {
      setLoadingLatest(false);
    }
  };

  if (!selectedRepo) {
    return (
      <div className="flex flex-col items-center justify-center py-24 space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">No repo selected</h1>
        <p className="text-muted-foreground">Add and select a repository to generate reports.</p>
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
          <h1 className="text-2xl font-bold tracking-tight">Reports</h1>
          <p className="text-muted-foreground mt-1">
            Generate CISO or CTO/VP Eng reports from scan results
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={loadLatest} disabled={loadingLatest}>
            {loadingLatest ? "Loading..." : "Load Latest"}
          </Button>
          <Button onClick={generateReport} disabled={generating}>
            {generating ? "Generating..." : "Generate Report"}
          </Button>
        </div>
      </div>

      <Tabs value={reportType} onValueChange={(v) => { setReportType(v as "ciso" | "cto"); setReport(null); }}>
        <TabsList>
          <TabsTrigger value="ciso">CISO Report</TabsTrigger>
          <TabsTrigger value="cto">CTO/VP Eng Report</TabsTrigger>
        </TabsList>
      </Tabs>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
            {error.includes("404") && (
              <p className="text-muted-foreground text-xs mt-2">
                No scan data found. Run a scan from the Dashboard first, then generate a report.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {!report && !generating && !error && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <p className="text-lg font-medium text-muted-foreground">
              {reportType === "ciso" ? "CISO Security Posture & Compliance Report" : "CTO/VP Eng Efficiency & ROI Report"}
            </p>
            <p className="text-sm text-muted-foreground mt-1">
              Click &ldquo;Generate Report&rdquo; to create a report from the latest scan data,
              or &ldquo;Load Latest&rdquo; to view a previously generated report.
            </p>
          </CardContent>
        </Card>
      )}

      {report && reportType === "ciso" && <CISOReport data={report} />}
      {report && reportType === "cto" && <CTOReport data={report} />}
    </div>
  );
}
