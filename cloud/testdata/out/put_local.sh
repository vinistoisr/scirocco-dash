#!/bin/sh
# Run from cloud/ -- loads testdata into wrangler dev's local R2
set -e
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/drive.csv.gz" --file "testdata/out/drives/2026/08/2026-08-15_0812/drive.csv.gz" --local
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/bursts.csv.gz" --file "testdata/out/drives/2026/08/2026-08-15_0812/bursts.csv.gz" --local
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-15_0812/meta.json" --file "testdata/out/drives/2026/08/2026-08-15_0812/meta.json" --local
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/drive.csv.gz" --file "testdata/out/drives/2026/08/2026-08-21_1743/drive.csv.gz" --local
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/bursts.csv.gz" --file "testdata/out/drives/2026/08/2026-08-21_1743/bursts.csv.gz" --local
npx wrangler r2 object put "scirocco-drives/drives/2026/08/2026-08-21_1743/meta.json" --file "testdata/out/drives/2026/08/2026-08-21_1743/meta.json" --local
