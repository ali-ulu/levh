# Public demo deployment

A throwaway, publicly reachable LEVH instance seeded with fake demo data
(`levh seed-demo`) for people to try before installing. **Not for real data —
see the warnings in `fly.toml`.**

Fly.io's free tier runs Docker containers directly (Cloudflare Workers can't
run this stack — no real filesystem/SQLite, and heavier Python deps like
`cryptography` don't work in its WASM sandbox). `*.fly.dev` gets free HTTPS
automatically; Cloudflare is optional on top, for a custom domain.

## 1. Deploy (needs your own Fly.io account)

```bash
# Install the CLI and sign in — this step needs your browser, I can't do it.
curl -L https://fly.io/install.sh | sh
flyctl auth login

# From the repo root:
flyctl launch --config deploy/demo/fly.toml --no-deploy   # confirm the app name is free
flyctl deploy --config deploy/demo/fly.toml
```

Check Fly.io's current free-tier limits before deploying — they change the
terms periodically and I can't verify today's numbers from here.

Your demo will be live at `https://<app-name>.fly.dev`.

## 2. (Optional) Put Cloudflare in front for a custom domain

1. Add your domain to Cloudflare (free plan).
2. Add a CNAME record: `demo` → `<app-name>.fly.dev`, proxy status **ON**
   (orange cloud) — Cloudflare terminates TLS and proxies to Fly.
3. In Fly, no certificate changes needed; Cloudflare's edge is what visitors
   hit.

## 3. Reset behavior

No persistent volume is mounted, and `auto_stop_machines`/`auto_start_machines`
are on. When the machine goes idle (no traffic) it stops; the next visitor's
request wakes a fresh machine, which re-runs `levh seed-demo --force` before
serving. Any changes a visitor made are gone — this is intentional, not a bug.

## 4. Tear down

```bash
flyctl apps destroy <app-name>
```
