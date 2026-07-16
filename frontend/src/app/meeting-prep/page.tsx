"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { MeetingPrep } from "@/types";
import {
  CalendarClock,
  Gavel,
  ListChecks,
  Loader2,
  Search,
  Users,
} from "lucide-react";

export default function MeetingPrepPage() {
  const [prep, setPrep] = useState<MeetingPrep | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");

  const load = useCallback(async (q: string) => {
    setLoading(true);
    try {
      setPrep((await api.meetingPrep(q)).meeting_prep);
    } catch {
      setPrep(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  const meeting = prep?.meeting ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Meeting Prep</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Your proactive &ldquo;before you walk in&rdquo; brief — the next meeting,
          who&apos;s attending, what you last discussed with each of them, and the
          open commitments and decisions that matter. Deterministic, offline.
        </p>
      </div>

      <form
        className="flex gap-2 max-w-lg"
        onSubmit={(e) => {
          e.preventDefault();
          load(query.trim());
        }}
      >
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="Find a specific meeting, or leave blank for the next one…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button type="submit" variant="outline">
          Prep
        </Button>
      </form>

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : !meeting ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground space-y-2">
            <CalendarClock className="h-8 w-8 mx-auto opacity-40" />
            <p>{prep?.reason ?? "No upcoming meeting found."}</p>
            <p>
              Import a calendar (.ics) in{" "}
              <a href="/settings/" className="underline">
                Settings
              </a>{" "}
              — upcoming events power this brief.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {/* The meeting */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <CalendarClock className="h-4 w-4 text-primary" />
                {meeting.title}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
                {meeting.when && <span>{meeting.when}</span>}
                {meeting.project && (
                  <Badge variant="outline" className="text-[11px]">
                    {meeting.project}
                  </Badge>
                )}
                <span className="text-[11px]">· {prep?.reason}</span>
              </div>
              {meeting.attendees.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {meeting.attendees.map((a) => (
                    <Badge key={a} variant="secondary" className="text-[11px]">
                      {a}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Who you're meeting */}
          {prep!.people.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  Who you&apos;re meeting
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {prep!.people.map((p) => (
                  <div key={p.email ?? p.name} className="border rounded-lg p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-sm">{p.name}</span>
                      <span className="text-[11px] text-muted-foreground">
                        {p.interaction_count > 0
                          ? `${p.interaction_count} prior · last ${p.last_seen}`
                          : "no prior interactions"}
                      </span>
                    </div>
                    {p.recent.length > 0 && (
                      <ul className="mt-1.5 space-y-1">
                        {p.recent.map((r) => (
                          <li key={r.id} className="text-xs text-muted-foreground flex gap-1.5">
                            <span className="text-muted-foreground/60">{r.date}</span>
                            <span className="line-clamp-1">{r.summary}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* Open commitments */}
          {prep!.open_commitments.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <ListChecks className="h-4 w-4" />
                  Relevant open commitments
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {prep!.open_commitments.map((c) => (
                    <li key={c.id} className="text-sm flex items-start gap-2">
                      <ListChecks className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                      <span>
                        {c.text}{" "}
                        <span className="text-[11px] text-muted-foreground">({c.date})</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {/* Recent decisions */}
          {prep!.recent_decisions.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <Gavel className="h-4 w-4" />
                  Recent decisions
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {prep!.recent_decisions.map((d) => (
                    <li key={d.id} className="text-sm flex items-start gap-2">
                      <Gavel className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                      <span>
                        {d.text}{" "}
                        <span className="text-[11px] text-muted-foreground">({d.date})</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
