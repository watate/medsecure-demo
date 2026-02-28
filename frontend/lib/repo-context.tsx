"use client";

import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { api, type Repo } from "@/lib/api";

interface RepoContextValue {
  /** Currently selected repo full_name (e.g. "owner/repo"), or null if none */
  selectedRepo: string | null;
  /** Set the selected repo and persist to localStorage */
  setSelectedRepo: (repo: string | null) => void;
  /** List of tracked (added) repos from the backend */
  trackedRepos: Repo[];
  /** Reload tracked repos from backend */
  refreshTrackedRepos: () => Promise<void>;
  /** Whether tracked repos are loading */
  loading: boolean;
}

const RepoContext = createContext<RepoContextValue | null>(null);

const STORAGE_KEY = "medsecure-selected-repo";

export function RepoProvider({ children }: { children: React.ReactNode }) {
  const [selectedRepo, setSelectedRepoState] = useState<string | null>(null);
  const [trackedRepos, setTrackedRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // Hydrate from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      setSelectedRepoState(stored);
    }
    setHydrated(true);
  }, []);

  const setSelectedRepo = useCallback((repo: string | null) => {
    setSelectedRepoState(repo);
    if (repo) {
      localStorage.setItem(STORAGE_KEY, repo);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const refreshTrackedRepos = useCallback(async () => {
    setLoading(true);
    try {
      const repos = await api.listTrackedRepos();
      setTrackedRepos(repos);
    } catch {
      // Ignore errors — repos list is best-effort
    } finally {
      setLoading(false);
    }
  }, []);

  // Load tracked repos on mount
  useEffect(() => {
    if (hydrated) {
      refreshTrackedRepos();
    }
  }, [hydrated, refreshTrackedRepos]);

  // If the selected repo is no longer in the tracked list, auto-select the first one
  useEffect(() => {
    if (!hydrated || loading || trackedRepos.length === 0) return;
    const found = trackedRepos.some((r) => r.full_name === selectedRepo);
    if (!found) {
      setSelectedRepo(trackedRepos[0].full_name);
    }
  }, [hydrated, loading, trackedRepos, selectedRepo, setSelectedRepo]);

  return (
    <RepoContext.Provider
      value={{
        selectedRepo,
        setSelectedRepo,
        trackedRepos,
        refreshTrackedRepos,
        loading,
      }}
    >
      {children}
    </RepoContext.Provider>
  );
}

export function useRepo() {
  const ctx = useContext(RepoContext);
  if (!ctx) {
    throw new Error("useRepo must be used within a RepoProvider");
  }
  return ctx;
}
