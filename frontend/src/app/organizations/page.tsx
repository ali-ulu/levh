"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import type { Memory, Organization } from "@/types";
import { ArrowLeft, Building2, Loader2, Search } from "lucide-react";

export default function OrganizationsPage() {
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  const [selected, setSelected] = useState<Organization | null>(null);
  const [orgMemories, setOrgMemories] = useState<Memory[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerMemory, setDrawerMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setOrgs((await api.listOrganizations()).organizations);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openOrg = async (o: Organization) => {
    setSelected(o);
    setDetailLoading(true);
    try {
      setOrgMemories((await api.getOrganization(o.key)).memories);
    } catch {
      setOrgMemories([]);
    }
    setDetailLoading(false);
  };

  const filtered = orgs.filter(
    (o) =>
      !filter ||
      o.name.toLowerCase().includes(filter.toLowerCase()) ||
      o.domain.toLowerCase().includes(filter.toLowerCase())
  );

  // ── Detail view ──────────────────────────────────────────────────
  if (selected) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
          <ArrowLeft className="h-4 w-4 mr-1.5" />
          All organizations
        </Button>

        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Building2 className="h-6 w-6 text-primary" />
            {selected.name}
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            <span className="font-mono">{selected.domain}</span> · {selected.memory_count} memories ·{" "}
            {selected.person_count} people
            {selected.last_seen && <> · last seen {selected.last_seen.slice(0, 10)}</>}
          </p>
        </div>

        {selected.people.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">People</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1.5">
                {selected.people.map((name) => (
                  <Badge key={name} variant="secondary" className="text-[11px]">
                    {name}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Memories mentioning {selected.name}</CardTitle>
          </CardHeader>
          <CardContent>
            {detailLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : orgMemories.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">No memories.</p>
            ) : (
              <div className="space-y-2">
                {orgMemories.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => setDrawerMemory(m)}
                    className="w-full text-left border rounded-lg p-3 hover:bg-accent/40 transition-colors"
                  >
                    <p className="text-sm line-clamp-2">{m.content}</p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      {m.source && (
                        <Badge variant="outline" className="text-[11px]">
                          {m.source.replace("connector:", "")}
                        </Badge>
                      )}
                      <span className="text-[11px] text-muted-foreground">
                        {(m.created_at || "").slice(0, 10)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {drawerMemory && (
          <MemoryDetailDrawer memory={drawerMemory} onClose={() => setDrawerMemory(null)} />
        )}
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Organizations</h1>
        <p className="text-sm text-muted-foreground mt-1">
          The companies you actually interact with — grouped automatically by the
          email domain of people across your calendar, email, and transcripts.
          Personal email providers are excluded.
        </p>
      </div>

      <div className="relative max-w-md">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-8"
          placeholder="Filter by name or domain..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <Building2 className="h-8 w-8 mx-auto opacity-40" />
            <p>No organizations yet.</p>
            <p>
              Import a calendar, email (.mbox), or meeting transcript in{" "}
              <a href="/settings/" className="underline">
                Settings
              </a>{" "}
              — organizations are derived from people&apos;s email domains.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((o) => (
            <button
              key={o.key}
              onClick={() => openOrg(o)}
              className="text-left border rounded-lg p-3 bg-card hover:bg-accent/40 transition-colors"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm truncate flex items-center gap-1.5">
                  <Building2 className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  {o.name}
                </span>
                <Badge variant="secondary" className="text-[11px] shrink-0">
                  {o.memory_count}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground font-mono truncate mt-0.5">{o.domain}</p>
              <div className="flex items-center justify-between mt-2">
                <span className="text-[11px] text-muted-foreground">
                  {o.person_count} {o.person_count === 1 ? "person" : "people"}
                </span>
                {o.last_seen && (
                  <span className="text-[11px] text-muted-foreground">{o.last_seen.slice(0, 10)}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
