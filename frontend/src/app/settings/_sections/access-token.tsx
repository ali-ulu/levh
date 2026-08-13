"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { clearToken, getToken, onTokenChange, setToken } from "@/lib/token";
import { KeyRound, Server } from "lucide-react";

export function AccessToken() {
    const [tokenSet, setTokenSet] = useState(false);
    const [tokenInput, setTokenInput] = useState("");
    // Keeps the badge honest when another tab changes the stored token.
    useEffect(() => {
      const sync = () => setTokenSet(getToken() !== "");
      sync();
      return onTokenChange(sync);
    }, []);

    const saveToken = () => {
      setToken(tokenInput);
      setTokenInput("");
    };

  return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <KeyRound className="h-4 w-4" />
            Server access token
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted-foreground">Status</span>
            <Badge variant={tokenSet ? "default" : "secondary"} className="text-[11px]">
              {tokenSet ? "•••• set" : "none"}
            </Badge>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Access token</Label>
              <Input
                type="password"
                className="w-64"
                placeholder="Paste the server token"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
              />
            </div>
            <Button variant="outline" onClick={saveToken} disabled={!tokenInput.trim()}>
              Save token
            </Button>
            <Button variant="outline" onClick={() => clearToken()} disabled={!tokenSet}>
              Clear token
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
              Only needed when this server was started with LEVH_TOKEN. Legacy STACKMEMORY_TOKEN
              is still accepted. Stored locally in
            your browser.
          </p>
        </CardContent>
      </Card>
  );
}
