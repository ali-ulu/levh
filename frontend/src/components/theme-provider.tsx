"use client";

import * as React from "react";
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes";
import { THEME_STORAGE_KEY } from "@/lib/theme";

type ThemeProviderProps = React.ComponentProps<typeof NextThemesProvider>;

/**
 * Mirrors the resolved theme into a cookie. `next-themes` only persists to
 * localStorage, which is invisible to the server and unavailable in some
 * private-browsing contexts; the cookie gives the pre-paint script in
 * `layout.tsx` a second source so the theme survives both.
 */
function ThemeCookieSync() {
  const { resolvedTheme } = useTheme();

  React.useEffect(() => {
    if (!resolvedTheme) return;
    document.cookie = `${THEME_STORAGE_KEY}=${resolvedTheme}; path=/; max-age=31536000; samesite=lax`;
  }, [resolvedTheme]);

  return null;
}

export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider {...props}>
      <ThemeCookieSync />
      {children}
    </NextThemesProvider>
  );
}
