"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type GitHubRepoInfo, type Repo } from "@/lib/api";
import { useRepo } from "@/lib/repo-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function ReposPage() {
  const { trackedRepos, refreshTrackedRepos, setSelectedRepo, selectedRepo } = useRepo();
  const [available, setAvailable] = useState<GitHubRepoInfo[]>([]);
  const [search, setSearch] = useState("");
  const [loadingAvailable, setLoadingAvailable] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAvailable = useCallback(async () => {
    setLoadingAvailable(true);
    setError(null);
    try {
      const data = await api.listAvailableRepos(search || undefined);
      setAvailable(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load repos");
    } finally {
      setLoadingAvailable(false);
    }
  }, [search]);

  // Load available repos on mount
  useEffect(() => {
    loadAvailable();
  }, [loadAvailable]);

  const addRepo = async (fullName: string) => {
    setAdding(fullName);
    setError(null);
    try {
      const repo = await api.addRepo(fullName);
      await refreshTrackedRepos();
      // Auto-select the newly added repo if none selected
      if (!selectedRepo) {
        setSelectedRepo(repo.full_name);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add repo");
    } finally {
      setAdding(null);
    }
  };

  const removeRepo = async (repo: Repo) => {
    setRemoving(repo.id);
    setError(null);
    try {
      await api.removeRepo(repo.id);
      await refreshTrackedRepos();
      // If we removed the selected repo, clear selection
      if (selectedRepo === repo.full_name) {
        setSelectedRepo(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove repo");
    } finally {
      setRemoving(null);
    }
  };

  const trackedNames = new Set(trackedRepos.map((r) => r.full_name));

  // Filter available repos by search (client-side for instant feedback)
  const filteredAvailable = search
    ? available.filter(
        (r) =>
          r.full_name.toLowerCase().includes(search.toLowerCase()) ||
          (r.description && r.description.toLowerCase().includes(search.toLowerCase()))
      )
    : available;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Repositories</h1>
        <p className="text-muted-foreground mt-1">
          Manage which repositories MedSecure tracks. All alerts, remediations, and scans are per-repo.
        </p>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Tracked Repos */}
      <Card>
        <CardHeader>
          <CardTitle>Tracked Repositories</CardTitle>
          <CardDescription>
            {trackedRepos.length} repo{trackedRepos.length !== 1 ? "s" : ""} tracked
          </CardDescription>
        </CardHeader>
        <CardContent>
          {trackedRepos.length === 0 ? (
            <p className="text-center text-muted-foreground py-8">
              No repositories tracked yet. Add one from the list below.
            </p>
          ) : (
            <div className="divide-y divide-border">
              {trackedRepos.map((repo) => (
                <div
                  key={repo.id}
                  className="flex items-center justify-between py-3"
                >
                  <div>
                    <p className="font-medium text-sm">{repo.full_name}</p>
                    <p className="text-xs text-muted-foreground">
                      Default branch: {repo.default_branch} &middot; Added{" "}
                      {new Date(repo.added_at).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {selectedRepo === repo.full_name && (
                      <Badge variant="default">Active</Badge>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setSelectedRepo(repo.full_name)}
                      disabled={selectedRepo === repo.full_name}
                    >
                      Select
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => removeRepo(repo)}
                      disabled={removing === repo.id}
                    >
                      {removing === repo.id ? "Removing..." : "Remove"}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Available Repos from GitHub PAT */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Available Repositories</CardTitle>
              <CardDescription>
                Repositories your GitHub PAT has access to
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={loadAvailable}
              disabled={loadingAvailable}
            >
              {loadingAvailable ? "Loading..." : "Refresh"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input
            placeholder="Search repos..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          {loadingAvailable && available.length === 0 ? (
            <p className="text-center text-muted-foreground py-8">Loading repositories...</p>
          ) : filteredAvailable.length === 0 ? (
            <p className="text-center text-muted-foreground py-8">
              {search ? "No repos match your search" : "No repos found"}
            </p>
          ) : (
            <div className="divide-y divide-border max-h-[500px] overflow-y-auto pr-2">
              {filteredAvailable.map((repo) => {
                const isTracked = trackedNames.has(repo.full_name);
                const isAdding = adding === repo.full_name;

                return (
                  <div
                    key={repo.full_name}
                    className="flex items-center justify-between py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <a
                          href={repo.html_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-sm hover:underline"
                        >
                          {repo.full_name}
                        </a>
                        {repo.private && (
                          <Badge variant="outline">Private</Badge>
                        )}
                        {repo.language && (
                          <Badge variant="secondary">{repo.language}</Badge>
                        )}
                      </div>
                      {repo.description && (
                        <p className="text-xs text-muted-foreground truncate mt-0.5">
                          {repo.description}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 ml-4">
                      {isTracked ? (
                        <Badge variant="default">Tracked</Badge>
                      ) : (
                        <Button
                          size="sm"
                          onClick={() => addRepo(repo.full_name)}
                          disabled={isAdding}
                        >
                          {isAdding ? "Adding..." : "Add"}
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
