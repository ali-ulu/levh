"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { toast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type {
  WikiLintResponse,
  WikiPage,
  WikiSearchHit,
  WikiStatus,
} from "@/types";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  FileText,
  Loader2,
  Search,
} from "lucide-react";

export default function WikiPageView() {
  const t = useT();

  const [status, setStatus] = useState<WikiStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<WikiSearchHit[] | null>(null);
  const [searchedFor, setSearchedFor] = useState("");

  const [page, setPage] = useState<WikiPage | null>(null);
  const [pageLoading, setPageLoading] = useState(false);

  const [lint, setLint] = useState<WikiLintResponse | null>(null);
  const [linting, setLinting] = useState(false);

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      setStatus(await api.wikiStatus());
    } catch {
      setStatus(null);
      toast.error(t("app.wiki.loadError"));
    }
    setStatusLoading(false);
  }, [t]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const runSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setPage(null);
    try {
      const res = await api.wikiSearch(q);
      setResults(res.results);
      setSearchedFor(res.query);
    } catch {
      setResults([]);
      setSearchedFor(q);
    }
    setSearching(false);
  }, [query]);

  const openPage = useCallback(
    async (hit: WikiSearchHit) => {
      setPageLoading(true);
      try {
        setPage(await api.wikiPage(hit.id));
      } catch {
        setPage(null);
        toast.error(t("app.wiki.page.loadError"));
      }
      setPageLoading(false);
    },
    [t]
  );

  const runLint = useCallback(async () => {
    setLinting(true);
    try {
      setLint(await api.wikiLint());
    } catch {
      setLint(null);
      toast.error(t("app.wiki.loadError"));
    }
    setLinting(false);
  }, [t]);

  const metrics: { labelKey: string; value: number }[] = status
    ? [
        { labelKey: "app.wiki.status.memories", value: status.memories },
        { labelKey: "app.wiki.status.snapshots", value: status.snapshots },
        { labelKey: "app.wiki.status.pages", value: status.pages },
      ]
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("app.wiki.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("app.wiki.subtitle")}
          </p>
        </div>
      </div>

      {statusLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : !status || !status.ready ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <BookOpen className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.wiki.notReady.title")}</p>
            <p>{t("app.wiki.notReady.hint")}</p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="secondary" className="text-[11px]">
                {t("app.wiki.status.ready")}
              </Badge>
              <Badge variant="outline" className="text-[11px]">
                {status.llm_backend_enabled
                  ? t("app.wiki.status.llmOn")
                  : t("app.wiki.status.llmOff")}
              </Badge>
              <span className="text-[11px] text-muted-foreground font-mono">
                {status.root_name}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-3 max-w-md">
              {metrics.map((m) => (
                <div key={m.labelKey}>
                  <div className="text-xl font-semibold tabular-nums">
                    {m.value}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {t(m.labelKey)}
                  </div>
                </div>
              ))}
            </div>
            {status.built_at && (
              <p className="text-[11px] text-muted-foreground">
                {t("app.wiki.status.builtAt")}: {status.built_at}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <form
        className="flex gap-2 max-w-lg"
        onSubmit={(e) => {
          e.preventDefault();
          runSearch();
        }}
      >
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("app.wiki.search.placeholder")}
          aria-label={t("app.wiki.search.placeholder")}
        />
        <Button type="submit" disabled={searching || !query.trim()}>
          {searching ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Search className="h-4 w-4" />
          )}
          {t("app.wiki.search.button")}
        </Button>
      </form>

      {pageLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : page ? (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold break-words">
                  {page.title}
                </h2>
                <p className="text-[11px] text-muted-foreground font-mono break-all">
                  {page.path}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-[11px]">
                  {page.kind}
                </Badge>
                <Button variant="ghost" size="sm" onClick={() => setPage(null)}>
                  <ArrowLeft className="h-4 w-4" />
                  {t("app.wiki.page.back")}
                </Button>
              </div>
            </div>
            <pre className="text-xs bg-muted/50 rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
              {page.content}
            </pre>
          </CardContent>
        </Card>
      ) : results === null ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <FileText className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.wiki.page.empty.title")}</p>
            <p>{t("app.wiki.page.empty.hint")}</p>
          </CardContent>
        </Card>
      ) : results.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Search className="h-8 w-8 mx-auto opacity-40" />
            <p>{t("app.wiki.search.empty", { query: searchedFor })}</p>
            <p>{t("app.wiki.search.hint")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t("app.wiki.search.count", { count: results.length })}
          </p>
          {results.map((hit) => (
            <button
              key={hit.id}
              onClick={() => openPage(hit)}
              className="w-full text-left border rounded-lg p-3 bg-card hover:bg-accent transition-colors"
            >
              <div className="flex items-start gap-2.5">
                <FileText className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{hit.title}</span>
                    <Badge variant="outline" className="text-[11px]">
                      {hit.kind}
                    </Badge>
                  </div>
                  {hit.snippet && (
                    <p className="text-xs text-muted-foreground mt-1">
                      {hit.snippet}
                    </p>
                  )}
                  <p className="text-[11px] text-muted-foreground font-mono mt-1 break-all">
                    {hit.path}
                  </p>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-lg font-semibold">{t("app.wiki.lint.title")}</h2>
          <Button variant="outline" size="sm" onClick={runLint} disabled={linting}>
            {linting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <AlertTriangle className="h-4 w-4" />
            )}
            {t("app.wiki.lint.button")}
          </Button>
        </div>
        {lint && (
          <Card>
            <CardContent className="p-4 space-y-3">
              {lint.ok ? (
                <div className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  <span>{t("app.wiki.lint.ok")}</span>
                </div>
              ) : (
                <>
                  <p className="text-sm">
                    {t("app.wiki.lint.count", { count: lint.issue_count })}
                  </p>
                  {lint.truncated && (
                    <p className="text-[11px] text-muted-foreground">
                      {t("app.wiki.lint.truncated", {
                        count: lint.issues.length,
                      })}
                    </p>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-left text-muted-foreground">
                          <th className="py-1 pr-3 font-medium">
                            {t("app.wiki.lint.col.code")}
                          </th>
                          <th className="py-1 pr-3 font-medium">
                            {t("app.wiki.lint.col.path")}
                          </th>
                          <th className="py-1 font-medium">
                            {t("app.wiki.lint.col.detail")}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {lint.issues.map((issue, i) => (
                          <tr key={`${issue.path}-${i}`} className="border-t">
                            <td className="py-1 pr-3 font-mono align-top">
                              {issue.code}
                            </td>
                            <td className="py-1 pr-3 font-mono align-top break-all">
                              {issue.path}
                            </td>
                            <td className="py-1 align-top">{issue.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
