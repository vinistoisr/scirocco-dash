# cloud/ -- the all-Cloudflare drive dashboard

One Worker (`scirocco-dash`) + one R2 bucket (`scirocco-drives`). The car's
tee daemon uploads each drive's session files to R2 over home WiFi; this
Worker lists them and renders them with uPlot. No bundler, no npm
dependencies, no other vendor. Decisions in docs/PLAN-deck.md (Cloud
section).

    wrangler.toml              Worker config: R2 binding LOGS, static assets
    src/index.js               /api/drives (list) + /api/drive/:id/:file
    public/index.html          the whole dashboard, one file, uPlot inlined
    testdata/make_testdata.py  fake sessions for wrangler dev

## Deploy

From this directory (`cloud/`), with any Node >= 18:

    npx wrangler login
    npx wrangler r2 bucket create scirocco-drives
    npx wrangler deploy

`wrangler deploy` prints the URL. wrangler.toml also routes a custom
domain (`scirocco.example.com` -- change it to your own; created automatically on deploy because
the zone is in the same account); delete the `routes` block to run on
workers.dev only. Free plan is plenty: the Worker only runs for /api/*
(static assets are served off-Worker), and 100k requests/day vs a
one-viewer dashboard is three orders of magnitude of headroom. The bucket
fits roughly 4 years of drives inside the free 10 GB, with zero egress
fees.

Redeploying after an edit is `npx wrangler deploy` again; there is no build
step.

## R2 object layout (the contract with the car)

    drives/YYYY/MM/<session_id>/
        drive.csv.gz     1 Hz baseline rows
        bursts.csv.gz    full-rate rows, leading `segment` column
        raw.bin.gz       every byte both directions (archive; never served)
        meta.json        start/end, VIN, DTCs, counters -- uploaded LAST

`meta.json` is the **commit marker**. The uploader must push it after the
other objects succeed; `/api/drives` ignores any session directory without
one, so a drive that lost WiFi mid-upload never shows up half-loaded and
simply completes on the retry. `session_id` must start with
`YYYY-MM-DD_HHMM` -- the Worker derives the `YYYY/MM/` path from the id, and
the listing's newest-first order is a plain string sort on it.

`has_bursts` in the listing is a size heuristic (bursts.csv.gz bigger than
1 KB): a header-only file gzips to a couple hundred bytes, the shortest
real pull to tens of KB. The page never depends on it -- it always fetches
bursts.csv.gz and tolerates 404 and header-only alike.

## R2 token for the car uploader

The Termux uploader authenticates with a bucket-scoped token that never
expires (Cloudflare dashboard, not wrangler):

1. Dashboard -> R2 -> Manage R2 API Tokens -> Create API Token.
2. Permissions: **Object Read & Write**. Scope: **Apply to specific
   buckets only** -> `scirocco-drives`. TTL: **Forever**.
3. Copy the Access Key ID / Secret Access Key into rclone on the deck:

       [r2]
       type = s3
       provider = Cloudflare
       access_key_id = <access key id>
       secret_access_key = <secret access key>
       endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com

   (`<ACCOUNT_ID>` is on the R2 overview page.) Then in Termux:
   `chmod 600 ~/.config/rclone/rclone.conf`. The token can only touch
   objects in this one bucket -- it cannot list buckets, create them, or
   see the rest of the account -- so a stolen head unit costs at worst the
   drive logs.

Uploader order per session: `raw.bin.gz`, `bursts.csv.gz`, `drive.csv.gz`,
then `meta.json` last.

## Local testing (before any real data exists)

    python testdata/make_testdata.py

generates two sessions under `testdata/out/` in the exact R2 layout -- a
quiet drive (no burst trigger ever fires) and a spirited one (three pulls,
segmented by the PLAN's actual trigger logic run over 14 Hz simulated
data). Load them into wrangler's local R2 (run from `cloud/`; the script
also writes these into `testdata/out/put_local.ps1` / `.sh`):

    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/drive.csv.gz"  --file "testdata/out/drives/2026/08/2026-08-15_0812/drive.csv.gz"  --local
    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/bursts.csv.gz" --file "testdata/out/drives/2026/08/2026-08-15_0812/bursts.csv.gz" --local
    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/meta.json"     --file "testdata/out/drives/2026/08/2026-08-15_0812/meta.json"     --local
    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/drive.csv.gz"  --file "testdata/out/drives/2026/08/2026-08-21_1743/drive.csv.gz"  --local
    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/bursts.csv.gz" --file "testdata/out/drives/2026/08/2026-08-21_1743/bursts.csv.gz" --local
    npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/meta.json"     --file "testdata/out/drives/2026/08/2026-08-21_1743/meta.json"     --local

then

    npx wrangler dev

and open http://localhost:8787. The 17:43 drive should show three shaded
burst bands with dense traces inside them; the 08:12 drive none. `--local`
state lives in `cloud/.wrangler/state`, shared with `wrangler dev`.

## Protecting the dashboard with Cloudflare Access

The dashboard is read-only but it is still a GPS trace of every drive, so
put Cloudflare Access (free up to 50 users) in front of it:

1. Quickest path for the workers.dev URL: dashboard -> Workers & Pages ->
   scirocco-dash -> Settings -> Domains & Routes -> workers.dev ->
   **Enable Cloudflare Access**. That creates the Access application and a
   policy allowing only your email (login by one-time PIN).
2. For the custom domain: Zero Trust dashboard -> Access -> Applications
   -> Add an application -> Self-hosted, application domain
   `scirocco.example.com`, policy Allow -> Emails -> your address.
3. To give the tuner access, add their email to the same Allow policy (or
   a second policy); they get the one-time-PIN flow, no account needed.
   Remove the email to revoke.

Access covers `/api/*` and the page alike since it sits in front of the
whole hostname. Keep both hostnames behind Access or delete the one you do
not use -- an unprotected workers.dev route would bypass the protected
custom domain.

## Notes for whoever edits the dashboard

- `public/index.html` is self-contained: uPlot 1.6.32 JS+CSS are inlined
  (MIT, license header kept with the code). To upgrade uPlot, replace the
  two inlined blocks with the new `dist/uPlot.min.css` /
  `dist/uPlot.iife.min.js` and keep the license comment.
- The page resolves CSV columns by normalized-name patterns, not fixed
  positions, and sniffs units (IAT logged in kelvin -> displayed in C,
  boost in mbar -> bar), because deck/tee.py's exact header spelling was
  not frozen when this was written. If a channel stops plotting after a
  tee change, the regexes in `CHANNELS` (top of the app script) are the
  place to look.
- Baseline and burst rows merge into one series per channel over the union
  timeline; burst rows win where both exist. Shaded bands mark segments,
  numbered on the top panel.
- The `.csv.gz` objects are served with `Content-Encoding: gzip`
  (`encodeBody: "manual"`), so the browser gunzips them natively; the page
  falls back to `DecompressionStream` if the bytes still carry the gzip
  magic when they arrive.
