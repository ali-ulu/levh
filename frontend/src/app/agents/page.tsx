"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useT } from "@/lib/i18n";

interface AgentActivity {
  id: string;
  agent_name: string;
  agent_display: string;
  session_id: string | null;
  project: string | null;
  status: string;
  connected_at: string;
  last_heartbeat_at: string;
  disconnected_at: string | null;
  online: boolean;
  metadata_json: string;
}

interface AgentStats {
  total_connections: number;
  currently_online: number;
  online_agents: { agent_name: string; display: string }[];
  by_agent: {
    agent_name: string;
    agent_display: string;
    connection_count: number;
    sessions: number;
    first_seen: string;
    last_seen: string;
  }[];
}

interface Checkpoint {
  id: string;
  agent_name: string;
  session_id: string | null;
  project: string | null;
  checkpoint_type: string;
  title: string;
  summary: string;
  memory_ids_json: string;
  created_at: string;
}

const AGENT_ICONS: Record<string, string> = {
  "claude-code": "🤖",
  "claude-desktop": "🧠",
  cursor: "⚡",
  vscode: "💻",
  windsurf: "🌊",
  cline: "🔧",
  jcode: "📝",
  omp: "🥧",
  opencode: "🔓",
  codex: "🤖",
  hermes: "🏛️",
  connector: "🔗",
  dashboard: "📊",
  cli: "⌨️",
  api: "🌐",
  unknown: "❓",
};

function getAgentIcon(name: string): string {
  return AGENT_ICONS[name] || "❓";
}

export default function AgentsPage() {
  const t = useT();
  const timeAgo = (dateStr: string): string => {
    if (!dateStr) return t("app.agents.time.unknown");
    const date = new Date(dateStr);
    const now = new Date();
    const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (seconds < 60) return t("app.agents.time.seconds", { count: seconds });
    if (seconds < 3600) return t("app.agents.time.minutes", { count: Math.floor(seconds / 60) });
    if (seconds < 86400) return t("app.agents.time.hours", { count: Math.floor(seconds / 3600) });
    return t("app.agents.time.days", { count: Math.floor(seconds / 86400) });
  };

  const [stats, setStats] = useState<AgentStats | null>(null);
  const [activities, setActivities] = useState<AgentActivity[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [statsRes, agentsRes, checkpointsRes] = await Promise.all([
        fetch("/api/agents/stats"),
        fetch("/api/agents?limit=50"),
        fetch("/api/checkpoints?limit=20"),
      ]);

      if (statsRes.ok) setStats(await statsRes.json());
      if (agentsRes.ok) setActivities(await agentsRes.json());
      if (checkpointsRes.ok) setCheckpoints(await checkpointsRes.json());
    } catch (err) {
      console.error("Failed to fetch agent data:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
    // Refresh every 30 seconds
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-muted-foreground">{t("app.agents.loading")}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">{t("app.agents.title")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("app.agents.subtitle")}
        </p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("app.agents.currentlyOnline")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {stats?.currently_online || 0}
            </div>
            {stats?.online_agents && stats.online_agents.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {stats.online_agents.map((a) => (
                  <Badge key={a.agent_name} variant="default" className="text-xs">
                    {getAgentIcon(a.agent_name)} {a.display}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("app.agents.totalConnections")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {stats?.total_connections || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t("app.agents.allTimeConnections")}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("app.agents.activeAgents")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {stats?.by_agent?.length || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t("app.agents.uniqueAgents")}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Per-Agent Breakdown */}
      {stats?.by_agent && stats.by_agent.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("app.agents.breakdown")}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {stats.by_agent.map((agent) => (
                <div
                  key={agent.agent_name}
                  className="flex items-center justify-between p-3 rounded-lg border"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">
                      {getAgentIcon(agent.agent_name)}
                    </span>
                    <div>
                      <div className="font-medium">{agent.agent_display}</div>
                      <div className="text-xs text-muted-foreground">
                        {t("app.agents.firstSeen", { time: timeAgo(agent.first_seen) })}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-medium">
                      {t(
                        agent.connection_count === 1
                          ? "app.agents.connections.one"
                          : "app.agents.connections.other",
                        { count: agent.connection_count },
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {t(
                        agent.sessions === 1
                          ? "app.agents.sessions.one"
                          : "app.agents.sessions.other",
                        { count: agent.sessions },
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent Activity */}
      <Card>
        <CardHeader>
          <CardTitle>{t("app.agents.recentActivity")}</CardTitle>
        </CardHeader>
        <CardContent>
          {activities.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">
              {t("app.agents.noActivity")}
            </p>
          ) : (
            <div className="space-y-2">
              {activities.map((activity) => (
                <div
                  key={activity.id}
                  className="flex items-center justify-between p-3 rounded-lg border"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xl">
                      {getAgentIcon(activity.agent_name)}
                    </span>
                    <div>
                      <div className="font-medium">
                        {activity.agent_display}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {activity.project
                          ? t("app.agents.project", { project: activity.project })
                          : t("app.agents.noProject")}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <Badge
                      variant={activity.online ? "default" : "secondary"}
                    >
                      {activity.online ? t("app.agents.online") : t("app.agents.offline")}
                    </Badge>
                    <div className="text-xs text-muted-foreground mt-1">
                      {activity.disconnected_at
                        ? t("app.agents.disconnected", { time: timeAgo(activity.disconnected_at) })
                        : t("app.agents.connected", { time: timeAgo(activity.connected_at) })}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Checkpoints */}
      <Card>
        <CardHeader>
          <CardTitle>{t("app.agents.recentCheckpoints")}</CardTitle>
        </CardHeader>
        <CardContent>
          {checkpoints.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">
              {t("app.agents.noCheckpoints")}
            </p>
          ) : (
            <div className="space-y-2">
              {checkpoints.map((cp) => (
                <div
                  key={cp.id}
                  className="flex items-center justify-between p-3 rounded-lg border"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xl">
                      {getAgentIcon(cp.agent_name)}
                    </span>
                    <div>
                      <div className="font-medium">{cp.title}</div>
                      <div className="text-xs text-muted-foreground">
                        {cp.agent_name}
                        {cp.project ? ` · ${cp.project}` : ""}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <Badge variant="outline">{cp.checkpoint_type}</Badge>
                    <div className="text-xs text-muted-foreground mt-1">
                      {timeAgo(cp.created_at)}
                    </div>
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
