"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { clearToken, getToken, onTokenChange, setToken } from "@/lib/token";
import { useT } from "@/lib/i18n";
import { KeyRound, Server } from "lucide-react";

export function AccessToken() {
    const t = useT();
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
            {t("settings.accessToken.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted-foreground">{t("settings.accessToken.status")}</span>
            <Badge variant={tokenSet ? "default" : "secondary"} className="text-[11px]">
              {tokenSet ? t("settings.accessToken.statusSet") : t("settings.accessToken.statusNone")}
            </Badge>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">{t("settings.accessToken.label")}</Label>
              <Input
                type="password"
                className="w-64"
                placeholder={t("settings.accessToken.placeholder")}
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
              />
            </div>
            <Button variant="outline" onClick={saveToken} disabled={!tokenInput.trim()}>
              {t("settings.accessToken.save")}
            </Button>
            <Button variant="outline" onClick={() => clearToken()} disabled={!tokenSet}>
              {t("settings.accessToken.clear")}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
              {t("settings.accessToken.help")}
          </p>
        </CardContent>
      </Card>
  );
}
