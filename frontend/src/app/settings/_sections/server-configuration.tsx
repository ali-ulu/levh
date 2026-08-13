"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ServerConfig } from "@/types";
import { Loader2, Server } from "lucide-react";

interface ServerConfigurationProps {
  config: ServerConfig | null;
}

export function ServerConfiguration({ config }: ServerConfigurationProps) {
  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="h-4 w-4" />
            Server Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!config ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <div>
                <div className="text-xs text-muted-foreground">Embedder</div>
                <div className="flex items-center gap-2">
                  {config.embedder_mode}
                  {config.embedder_dimension && (
                    <Badge variant="outline" className="text-[11px]">{config.embedder_dimension}-d</Badge>
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Database</div>
                <div className="font-mono text-xs truncate">{config.db_path}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Short-term capacity</div>
                <div>{config.short_term_max}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Default half-life</div>
                <div>{config.decay_half_life_hours} h</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">H(x,ψ) weights</div>
                <div className="font-mono text-xs">
                  α={config.weights.alpha} β={config.weights.beta} γ={config.weights.gamma} δ={config.weights.delta}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Reinforcement gain</div>
                <div>+{(config.reinforcement_gain * 100).toFixed(0)}%–{(config.reinforcement_gain * 150).toFixed(0)}% per recall</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Max stability</div>
                <div>{(config.max_stability_hours / 24).toFixed(0)} days</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Auto-summarize sessions</div>
                <Badge variant={config.auto_summarize_sessions ? "default" : "secondary"} className="text-[11px]">
                  {config.auto_summarize_sessions ? "on" : "off"}
                </Badge>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Version</div>
                <div>{config.version}</div>
              </div>
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">
            Every memory has its own half-life that starts at the default and grows every time
            it&apos;s recalled or reinforced — like spaced repetition. Configure via environment
            variables (<code className="bg-muted px-1 rounded">.env</code>): EMBEDDER_MODE,
            SQLITE_DB_PATH, SHORT_TERM_MAX, DECAY_HALF_LIFE_HOURS, HSCORE_ALPHA…DELTA,
            REINFORCEMENT_GAIN, MAX_STABILITY_HOURS, AUTO_SUMMARIZE_SESSIONS.
          </p>
        </CardContent>
      </Card>
  );
}
