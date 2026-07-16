import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthGate } from "@/components/auth-gate";

export const metadata: Metadata = {
  title: "LEVH",
  description: "LEVH — local-first context continuity for AI work.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased">
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange={false}>
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
        </ThemeProvider>
      </body>
    </html>
  );
}
