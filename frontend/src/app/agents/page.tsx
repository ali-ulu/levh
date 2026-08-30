"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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

function timeAgo(dateStr: string): string {
  if (!dateStr) return "unknown";
  const date = new Date(dateStr);
  const now = new Date();
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function AgentsPage() {
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [activities, setActivities] = useState<AgentActivity[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
    // Refresh every 30 seconds
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  async function fetchData() {
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
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-muted-foreground">Loading agent activity...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Agent Activity</h1>
        <p className="text-muted-foreground mt-1">
          Real-time view of connected AI agents and their activity
        </p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Currently Online
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
              Total Connections
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {stats?.total_connections || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              All-time agent connections
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Active Agents
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {stats?.by_agent?.length || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Unique agents that have connected
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Per-Agent Breakdown */}
      {stats?.by_agent && stats.by_agent.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Agent Breakdown</CardTitle>
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
                        First seen: {timeAgo(agent.first_seen)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-medium">
                      {agent.connection_count} connections
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {agent.sessions} sessions
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
          <CardTitle>Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          {activities.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">
              No agent activity yet. Agents will appear here when they connect
              to LEVH.
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
                          ? `Project: ${activity.project}`
                          : "No project"}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <Badge
                      variant={activity.online ? "default" : "secondary"}
                    >
                      {activity.online ? "🟢 Online" : "⚪ Offline"}
                    </Badge>
                    <div className="text-xs text-muted-foreground mt-1">
                      {activity.disconnected_at
                        ? `Disconnected ${timeAgo(activity.disconnected_at)}`
                        : `Connected ${timeAgo(activity.connected_at)}`}
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
          <CardTitle>Recent Checkpoints</CardTitle>
        </CardHeader>
        <CardContent>
          {checkpoints.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">
              No checkpoints yet. Agents can create checkpoints during important
              work.
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
