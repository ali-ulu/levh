import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { ThemeProvider } from "@/components/theme-provider";
import { THEME_STORAGE_KEY, themeBootstrapScript } from "@/lib/theme";
import { AuthGate } from "@/components/auth-gate";
import { Toaster } from "@/components/ui/toast";
import { DEFAULT_LOCALE, translate } from "@/lib/i18n/translate";
import { LocaleProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: translate("app.metadata.title"),
  description: translate("app.metadata.description"),
  manifest: "/manifest.webmanifest",
  applicationName: translate("app.metadata.applicationName"),
  icons: [
    { rel: "icon", url: "/icon.png", type: "image/png" },
    { rel: "icon", url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    { rel: "icon", url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    { rel: "apple-touch-icon", url: "/apple-touch-icon.png", sizes: "180x180" },
  ],
  other: { "apple-mobile-web-app-capable": "yes", "mobile-web-app-capable": "yes" },
};

export const viewport: Viewport = {
  themeColor: "#4b56f7",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang={DEFAULT_LOCALE} suppressHydrationWarning>
      {/* Theme persistence (#567). next-themes writes the theme to
          localStorage *after* hydration, so a returning dark-theme user saw a
          light flash on every load. This blocking script runs before paint,
          reads the same key, and puts `class="dark"` on <html> first. It also
          mirrors the value into a cookie so the choice survives a context where
          localStorage is unavailable. Kept inline and tiny on purpose: it must
          run synchronously, before the browser paints anything. */}
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript() }} />
      </head>
      <body className="antialiased">
        <LocaleProvider>
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
            storageKey={THEME_STORAGE_KEY}
          >
            <div className="app-canvas min-h-screen">
              <div className="ambient ambient-one" />
              <div className="ambient ambient-two" />
              <Sidebar />
              <div className="min-w-0 lg:ml-[248px]">
                <Header />
                <main className="relative z-10 mx-auto max-w-[1680px] p-4 sm:p-6 lg:p-8">
                  <AuthGate>{children}</AuthGate>
                </main>
              </div>
            </div>
            <Toaster />
          </ThemeProvider>
        </LocaleProvider>
        <Script id="levh-pwa-register" strategy="afterInteractive" src="/pwa-register.js" />
      </body>
    </html>
  );
}
