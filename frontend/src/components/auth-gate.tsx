"use client";

import { useCallback, useEffect, useState } from "react";
import { Lock, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { getToken, setToken, onTokenChange } from "@/lib/token";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type GateState = "checking" | "open" | "locked";

/**
 * Wraps the app and enforces the optional server access token.
 *
 * Behaviour:
 *  - While /api/health is in flight → minimal "Loading…" (avoids flashing the gate).
 *  - health throws → treat as unknown, render children (never block on a hiccup).
 *  - auth_required === false → render children.
 *  - auth_required === true + a stored token → render children (a wrong token
 *    surfaces as 401s in the normal UI, acceptable for this iteration).
 *  - auth_required === true + no token → render the token-entry card.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>("checking");
  const [value, setValue] = useState("");

  const check = useCallback(async () => {
    try {
      const health = await api.health();
      if (health.auth_required === true && !getToken()) {
        setState("locked");
      } else {
        setState("open");
      }
    } catch {
      // Health is never gated, so a failure here is a network/other hiccup —
      // don't hold the whole app hostage to it.
      setState("open");
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  // Re-evaluate when the token changes anywhere (e.g. entered here, or cleared
  // from the Settings page).
  useEffect(() => onTokenChange(check), [check]);

  const unlock = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setValue("");
    setToken(trimmed); // fires onTokenChange → check() re-runs
  }, [value]);

  if (state === "checking") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading…
      </div>
    );
  }

  if (state === "locked") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Lock className="h-4 w-4" />
              This LEVH server requires a token
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Ask the server operator for the access token, or check the
              STACKMEMORY_TOKEN value on the server.
            </p>
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                unlock();
              }}
            >
              <Input
                type="password"
                autoFocus
                placeholder="Access token"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
              <Button type="submit" disabled={!value.trim()}>
                Unlock
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    );
  }

  return <>{children}</>;
}
