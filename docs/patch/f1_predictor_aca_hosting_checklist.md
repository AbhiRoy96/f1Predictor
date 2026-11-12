---
title: Gridcast ACA hosting readiness checklist
description: Repository implementation status and staging evidence required before launch.
---

## Repository implementation status

This checklist is implemented as repository changes only. No Azure resources
have been provisioned or changed. Unchecked launch gates below require actual
staging, live-data, operator or legal evidence; configuration is not proof of deployment.

The original assessment and deployment sketch below describe the baseline, not
the current implementation. Use the checked launch items and implementation notes
when reviewing the updated code. For current deployment commands, use the
[Azure operator guide](../../infra/azure/README.md), not the historical sketch in
section 5.

- Queue admission now enforces three active jobs per user, ten globally,
  active de-duplication, one-hour successful-result reuse, season-sync single-flight
  and cooldown, and database-backed user/client-address write throttles.
- Hosted requests require an Entra-authenticated web proxy and a private shared
  credential to the internal API. The web and API cap JSON bodies at 16 KiB.
  Production requires ACA authentication; principal headers alone are not proof of sign-in.
- Production jobs run in killable subprocesses with a heartbeat and a one-hour
  default deadline. The parent retains the direct PostgreSQL advisory lock.
- The web includes a lightweight `/health` probe, provenance disclosures, a privacy
  notice and upstream data attribution. The proprietary font rewrite is removed.
- Recent-season results-only collection, calendar-time decay/form windows, a
  conditional top-10 ranking calibration objective, paired past-selected-best-baseline
  intervals and version-checked model artifacts are implemented with regression tests.
- Offline tools lock experiment inputs, compare 12/18/24/36-month/all-history
  windows and reserve the final test for one execution. Prospective forecasts are
  archived immutably and can be scored after results arrive.
- The enqueue-only scheduler records qualifying versus official-grid publication
  phases. Official-grid forecasts reject missing, imputed or qualifying-proxy
  grids before training. Genuine publication observations remain operator inputs.
- Local smoke checks verified web health `200`, anonymous API rejection `401`,
  and oversized JSON rejection `413`. Azure ingress and Entra validation remain pending.
- Bicep templates now define private PostgreSQL, VNet-integrated ACA, worker-only
  Azure Files, secret-scoped managed identities, Key Vault, probes, budgets and
  bounded logging. Web ingress stays internal until Entra configuration is verified.
- The preview-first release script stops the old worker, snapshots its shares,
  completes the DB-init job, waits for new revisions and verifies authentication
  before exposing web. Rollback preserves the additive schema.
- The runtime has 79 exact, hashed dependency pins. CI includes dependency/image
  scans and infrastructure validation. Protected manual OIDC releases build AMD64
  images tagged by SHA; opt-in daily snapshots prune only marked, expired snapshots.

See [hosted queue safeguards](../guide.md#hosted-queue-safeguards)
for behavior, configuration and migration details.

Local validation: Windows `230 passed, 1 skipped, 55 subtests passed`; a separate
locked Linux runtime run completed `228 tests, 0 failures, 0 errors, 6 skipped`.
The Linux run passed the
PostgreSQL integration test against a disposable PostgreSQL 17 database; its six
PowerShell release tests were skipped and validated separately on Windows.
Ten offline release/retention/lock tests pass, including exact snapshot timestamp
preservation and refusal to delete unmarked or unrelated snapshots.
The production Next.js build and three hosted-identity tests pass.
Both Bicep templates compile without warnings, workflow YAML parses, targeted
Python lint passes, and Docker build configuration checks pass. The existing Linux
runtime matches every locked package and passes `pip check`; a fresh production
image build remains unverified because package downloads fail TLS negotiation.
Local dependency audits remain unverified: npm's configured registry returned
`429`, and downloading the Python audit tool failed TLS negotiation. CI scans are
wired, not claimed clean.
These results do not replace the live collection, experiment, Entra, mount,
backup/restore, telemetry or rollback checks below.

## Baseline assessment

Scope: the `f1Predictor-dev-integration-agent` build (Next.js web, Flask API, Postgres, single prediction worker, OpenTelemetry, Python ML pipeline).

**How this was produced:** I read the Dockerfiles, `compose.yaml`, `backend/` (app, options, worker, tasks, db), `next.config.mjs`, `requirements.txt`, the CI workflow and the ML code, and ran the test suite (184 passed, 1 skipped; the skipped one needs a Postgres URL). I did **not** deploy anything, build the web app, or run the pipeline against live FastF1 data. Azure commands below are a sketch to verify in a staging environment, not tested.

---

## 1. Summary

| Area | State today | Needed before hosting |
|---|---|---|
| Authentication | None on the API or UI | Required (A1) |
| Heavy jobs reachable from the internet | Yes, via `POST /api/predictions` and `/api/seasons/<year>/sync` | Cap and protect (A2) |
| Telemetry stack | Local Grafana "lgtm" container with anonymous admin | Replace with Azure endpoints (A3) |
| State on disk (`data/`, `models/`, `reports/`, FastF1 cache) | Docker volumes | Azure Files plus a plan for the cache (A4) |
| Worker | Single instance enforced by a Postgres advisory lock | Keep one replica (A5) |
| Dependencies | Unpinned | Lock file and model-version checks (A6) |
| Image architecture | Unknown | Build `linux/amd64` (A7) |
| Model evidence | Phase 1 fixes done, data and ensemble work not done | Finish before public forecasts (section 3) |
| Data and branding terms | Unreviewed; one hotlinked F1 font | Review (section 6) |

---

## 2. Blockers (do before any public exposure)

### A1. Authentication

The API has no auth. Its comment says "No CORS is enabled; Next.js proxies same-origin requests", so the UI is the only intended client, but nothing stops a direct call.

- Make **web** the only app with external ingress.
- Turn on ACA **built-in authentication** (Microsoft Entra ID) for web, and set unauthenticated requests to redirect to sign-in (or return 401 for `/api/*`).
- Give **api** *internal* ingress only. Point the web app's `BACKEND_URL` at the API's internal address (use the FQDN shown by `az containerapp show -n gridcast-api -g $RG --query properties.configuration.ingress.fqdn`).
- If you later want a public read-only dashboard, expose only `GET` endpoints through the web app and keep `POST` behind a signed-in role.

**Check:** an unauthenticated `curl https://<web-fqdn>/api/jobs` is rejected, and `curl` to the API's address from outside the environment fails.

### A2. Cap and protect expensive operations

`backend/options.py` limits `n_simulations` to 100,000 and `events_per_season` to 30. For a hosted version:

- Lower `events_per_season` to what you actually use (6-12) and `n_simulations` to about 20,000-50,000.
- Allow only a few queued jobs per user, reject duplicates for the same `(year, event, stage, strength_model, seed)` while one is queued or running, and return the existing run if a fresh identical one exists.
- Season sync: one at a time, with a minimum interval per season.
- Put a request body size limit and a per-IP/per-user rate limit in front (ACA ingress plus the web layer).

### A3. No anonymous Grafana in the cloud

`compose.yaml` sets `GF_AUTH_ANONYMOUS_ENABLED: "true"` and `GF_AUTH_ANONYMOUS_ORG_ROLE: Admin`. Fine on `127.0.0.1`, unsafe anywhere reachable.

- Do not deploy the `lgtm` service to ACA.
- Telemetry in this repo is off unless `OTEL_ENABLED=true` (verified in `src/observability/telemetry.py`). In Azure, set it to `true` only with an exporter endpoint you control: an OTLP collector, the ACA managed OpenTelemetry agent, or Azure Monitor / Application Insights. Check which of those your environment supports before relying on one.
- Set `PYROSCOPE_ENABLED=false` and `GPU_METRICS_ENABLED=false`.
- Reduce trace sampling: `OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.1`.
- If you want Grafana, use Azure Managed Grafana with Entra auth.

### A4. Persistent state

The pipeline writes `data/raw`, `data/processed`, versioned `models/{run_id}/` and `reports/`, and FastF1 keeps a cache under `data/cache/fastf1` (the cache is SQLite plus files).

- Only the **worker** should need these. Check whether the API reads them anywhere (it should not once data is imported into Postgres). If it does not, mount them on the worker only.
- Use an Azure Files share mounted into the worker. If you use one share with `subPath` entries, verify your ACA version supports it; otherwise use three small shares.
- The container runs as UID 10001 (`gridcast`). SMB mounts need matching ownership: set mount options such as `uid=10001,gid=10001,dir_mode=0770,file_mode=0660` on the volume.
- **FastF1 cache on SMB:** SQLite file locking over SMB can fail or be slow. Test it. If it misbehaves, keep the FastF1 cache on the worker's local disk (it is rebuilt from the network) and keep only the processed Parquet and model artifacts on Azure Files.
- Back the share up (Azure Files snapshots), or be able to rebuild it from source data.

### A5. Exactly one worker replica

`backend/worker.py` takes `pg_try_advisory_lock(716421)` and exits with code 2 if another worker holds it. On startup it also marks every job still `running` as `failed`.

- Set the worker to min = max = 1 replica.
- A revision swap will start the new replica while the old one still holds the lock, so the new one will crash and restart until the old one stops. Either deactivate the old revision first, or accept the brief crash loop.
- Connect the worker to Postgres **directly (port 5432)**, not through PgBouncer in transaction mode, which breaks session-level advisory locks.
- Add a **maximum runtime per job** and a heartbeat. Today a stuck `running` job is only cleared when the worker restarts.

### A6. Reproducible dependencies

`requirements.txt` has no version pins for `fastf1`, `pandas`, `numpy`, `scikit-learn`, `xgboost`, `scipy`, `pyarrow`, `joblib`, `requests`, and others.

- Generate a hashed lock file (for example `uv pip compile requirements.txt --generate-hashes -o requirements.lock`) and install from it in the Dockerfile.
- Saved models are joblib files. Store the library versions in each model's metadata and **refuse to load** on a mismatch, to avoid silent errors or crashes after an upgrade.
- Keep `requirements.txt` for development; build images from the lock file.
- Add a dependency scan (Dependabot or `pip-audit`) and a container image scan (for example Trivy) to CI.

### A7. Image platform

ACA runs `linux/amd64`. If you build on an Apple Silicon Mac, build with `--platform linux/amd64` (or build in GitHub Actions or `az acr build`).

---

## 3. Model readiness (before showing forecasts to other people)

Phase 1 fixes from the v2 plan are in the code (grid proxy, grid baseline, DNF labels, quantiles, audit script). These are **not** done, and they matter more for forecast quality than anything in the hosting layer:

1. **Whole-season, results-only data collection** for the current and previous season (F7). Today the current season is about 6 of 19 races and the 2021 seasons get the same budget.
2. **Time-based recency** (decay in days, time-based form windows) and the **window ablation** (12 / 18 / 24 / 36 months / all).
3. **Paired comparison against the best baseline**, not only grid (F3), and the **top-10 Plackett-Luce temperature objective** (F5).
4. **Locked final test**: define a cut-off date, tune only on earlier data, evaluate once.
5. **Re-run `python main.py evaluate race 2026 "Brazilian Grand Prix" --stage post_quali`** after these changes and keep the output as the new reference. Also re-check that the quali and grid baselines are no longer uniform and that `dnf_brier_skill` is above 0.
6. **Show model limits in the UI:** stage, history scope (sampled or full), number of out-of-fold races behind calibration, model version and training end date, and a short "what this is not" note. If you state "beats grid", show the paired interval beside it.
7. **Live scoreboard**: after each race, score the stored forecast against the actual result and keep a running table. This is your honest long-run evidence.
8. **Scheduled forecasts**: run after qualifying and again after official grid penalties are published, as ACA Jobs on a cron schedule, and store the stage with each run.

Rule of thumb: don't put "win probability" numbers in front of the public until items 1-5 are done and the UI shows item 6.

---

## 4. Target layout on ACA

| Component | ACA resource | Ingress | Replicas | Starting size | Notes |
|---|---|---|---|---|---|
| Web (Next.js, port 3000) | Container App | External, Entra auth | 1-3 | 0.5 vCPU / 1 GiB | `BACKEND_URL` = API internal address; HTTP scale rule |
| API (gunicorn, port 5000) | Container App | **Internal** | 1-2 | 0.5 vCPU / 1 GiB | Probes on `/api/health`; no disk needed if data is in Postgres |
| Worker | Container App, no ingress | None | **1 (min = max)** | 2 vCPU / 4-8 GiB (measure) | Azure Files mount; command `python -m backend.worker` |
| DB init | Container Apps **Job**, manual trigger | n/a | n/a | 0.25 vCPU / 0.5 GiB | `flask --app backend.app:create_app db-init` on every deploy |
| Scheduled forecasts | Container Apps **Job**, cron trigger | n/a | n/a | Same as worker | Optional; must not run concurrently with the worker's disk writes (see A5 and A4) |
| Database | Azure Database for PostgreSQL Flexible Server | Private or firewalled | n/a | Burstable tier to start | SSL required, point-in-time restore on |
| Images | Azure Container Registry | n/a | n/a | Basic | Pull with managed identity, tag by git SHA |
| Secrets | Key Vault + managed identity | n/a | n/a | | Reference from ACA secrets |
| Telemetry | OTLP endpoint / Application Insights / Managed Grafana | n/a | n/a | | Sample at 10-20% |

**Sizing:** the worker's peak memory depends on how much data collection loads (laps and weather are large). Run one full forecast in staging and read peak memory and CPU before choosing the final size. The simulation itself (20,000 races of 22 drivers) is small.

### Environment variables by app

| Variable | Web | API | Worker | Value |
|---|---|---|---|---|
| `BACKEND_URL` | yes | | | API internal address |
| `DATABASE_URL` | | yes | yes | `postgresql+psycopg://<user>:<pwd>@<host>:5432/gridcast?sslmode=require` (from a Key Vault secret) |
| `OTEL_ENABLED` | yes | yes | yes | `true` only when an endpoint exists, otherwise `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` | yes | yes | yes | Your collector or Azure endpoint |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | yes | yes | yes | `parentbased_traceidratio`, `0.1` |
| `OTEL_SERVICE_NAME` | yes | yes | yes | `gridcast-web` / `gridcast-api` / `gridcast-worker` |
| `PYROSCOPE_ENABLED`, `GPU_METRICS_ENABLED` | | yes | yes | `false` |
| `ML_COMPUTE_HOURLY_USD` | | | yes | Your real price per vCPU-hour times vCPUs |

---

## 5. Deployment skeleton (verify before use)

Placeholders in `<>`. Run in a staging resource group first.

```bash
RG=gridcast-rg; LOC=westeurope; ENV=gridcast-env; ACR=<uniqueacrname>; TAG=$(git rev-parse --short HEAD)

az group create -n $RG -l $LOC
az acr create -n $ACR -g $RG --sku Basic

# Build for amd64 in Azure (avoids Apple Silicon problems)
az acr build -r $ACR -t gridcast-api:$TAG .
az acr build -r $ACR -t gridcast-web:$TAG ./web

az containerapp env create -n $ENV -g $RG -l $LOC

# Postgres (choose private access/VNet for production)
az postgres flexible-server create -g $RG -n <pgname> -l $LOC \
  --admin-user gridcast --admin-password '<from-key-vault>' --tier Burstable --sku-name Standard_B1ms
az postgres flexible-server db create -g $RG -s <pgname> -d gridcast

# Azure Files for the worker
az storage account create -g $RG -n <storageacct> -l $LOC --sku Standard_LRS
az storage share-rm create --storage-account <storageacct> -n gridcast-files
az containerapp env storage set -g $RG -n $ENV --storage-name gridcast-files \
  --azure-file-account-name <storageacct> --azure-file-account-key '<key>' \
  --azure-file-share-name gridcast-files --access-mode ReadWrite

# API: internal ingress
az containerapp create -g $RG -n gridcast-api --environment $ENV \
  --image $ACR.azurecr.io/gridcast-api:$TAG --registry-server $ACR.azurecr.io --registry-identity system \
  --ingress internal --target-port 5000 --min-replicas 1 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --secrets database-url='<url>' --env-vars DATABASE_URL=secretref:database-url OTEL_ENABLED=false

# Web: external ingress (then enable Entra auth, see below)
az containerapp create -g $RG -n gridcast-web --environment $ENV \
  --image $ACR.azurecr.io/gridcast-web:$TAG --registry-server $ACR.azurecr.io --registry-identity system \
  --ingress external --target-port 3000 --min-replicas 1 --max-replicas 3 --cpu 0.5 --memory 1.0Gi \
  --env-vars BACKEND_URL=http://<api-internal-address>

# One-off DB init job
az containerapp job create -g $RG -n gridcast-db-init --environment $ENV \
  --trigger-type Manual --replica-timeout 600 --replica-retry-limit 1 \
  --image $ACR.azurecr.io/gridcast-api:$TAG --registry-server $ACR.azurecr.io --registry-identity system \
  --cpu 0.25 --memory 0.5Gi --secrets database-url='<url>' --env-vars DATABASE_URL=secretref:database-url \
  --command "flask" --args "--app" "backend.app:create_app" "db-init"
az containerapp job start -g $RG -n gridcast-db-init
```

Entra authentication for the web app: configure it with `az containerapp auth microsoft update` (client ID, tenant, secret reference) and `az containerapp auth update --unauthenticated-client-action RedirectToLoginPage`. Check the current CLI syntax and your tenant policies before running.

**Worker (needs a volume, so use YAML):** the CLI cannot declare volume mounts with flags, so create it from a YAML file.

```yaml
# worker.yaml  (sketch)
properties:
  environmentId: <environment resource id>
  configuration:
    registries:
      - server: <acr>.azurecr.io
        identity: system
    secrets:
      - name: database-url
        keyVaultUrl: https://<kv>.vault.azure.net/secrets/database-url
        identity: system
  template:
    containers:
      - name: worker
        image: <acr>.azurecr.io/gridcast-api:<tag>
        command: ["python", "-m", "backend.worker"]
        resources: { cpu: 2.0, memory: 4Gi }
        env:
          - { name: DATABASE_URL, secretRef: database-url }
          - { name: OTEL_ENABLED, value: "false" }
          - { name: PYROSCOPE_ENABLED, value: "false" }
          - { name: GPU_METRICS_ENABLED, value: "false" }
        volumeMounts:
          - { volumeName: files, mountPath: /app/data,    subPath: data }
          - { volumeName: files, mountPath: /app/models,  subPath: models }
          - { volumeName: files, mountPath: /app/reports, subPath: reports }
    volumes:
      - name: files
        storageType: AzureFile
        storageName: gridcast-files
        mountOptions: uid=10001,gid=10001,dir_mode=0770,file_mode=0660
    scale: { minReplicas: 1, maxReplicas: 1 }
```

```bash
az containerapp create -g $RG -n gridcast-worker --yaml worker.yaml
```

Things to confirm: `subPath` support in your environment, that the managed identity has `AcrPull` on the registry and `Key Vault Secrets User` on the vault, and that the worker can write to the mount as UID 10001.

**CI/CD:** GitHub Actions with OIDC federation to Azure (no stored credentials), `az acr build`, `az containerapp update --image ...:<sha>`, then a smoke test (`/api/health`, and a small demo forecast through the queue). Keep the existing Postgres-backed test job and the frontend job.

---

## 6. Data, licensing and branding

This is general caution, not legal advice.

1. **Hotlinked F1 font.** `web/next.config.mjs` rewrites `/fonts/Formula1-Bold.woff2` to `https://www.formula1.com/s/fonts/formula1/Formula1-Bold.woff2`. That serves a proprietary Formula 1 font from your public site. Remove it and use a font you are licensed to use (the project already uses `@fontsource-variable/noto-sans`).
2. **Data terms.** Data comes from FastF1 and the Jolpica API. Read both projects' terms and the terms for Formula 1 timing data, particularly for a **public** or **commercial** site.
3. **Trademarks.** Avoid F1 logos and the "F1" mark as your product name or logo, and add a visible "unofficial, not affiliated with Formula 1 or any team" notice. Your repository title currently includes "F1".
4. **Be gentle with upstream APIs.** Add exponential backoff, a single-flight lock for season sync, and a conservative request rate so a hosted app cannot hammer Jolpica.
5. **Privacy.** If you use Entra sign-in, keep the logs free of user identifiers beyond what you need, and document what you store.

---

## 7. Operations

- **Health and probes:** startup, readiness and liveness probes on `/api/health` for the API. Add a basic probe for the web app. The Dockerfile has no `HEALTHCHECK`; ACA probes cover that.
- **gunicorn and OpenTelemetry:** the API runs two gunicorn workers. OpenTelemetry providers created before the fork often stop exporting in the child processes. After deploying, confirm that traces **and** metrics appear from both workers; if not, initialise telemetry in a `post_fork` hook.
- **Logging cost:** the app logs JSON. Set a retention period and a daily cap on the Log Analytics workspace, and a budget alert in Azure Cost Management.
- **Backups:** enable Postgres point-in-time restore, snapshot the Azure Files share, and test one restore.
- **Schema changes:** `backend/db.py` applies additive migrations through `create_all` plus a `schema_versions` table. That is fine for additive changes. Before you rename or drop columns, adopt a real migration tool and run migrations only from the init job, not on API startup.
- **Release safety:** keep the previous image tag, use revision labels or traffic splitting for a canary, and roll back by reactivating the old revision.
- **Cold starts:** web and API at min 1 replica avoid cold starts; scale-to-zero saves money but makes the first request slow.
- **Secrets hygiene:** rotate the database password, keep `.env` out of images (the repo has a `.dockerignore`; verify it excludes `.env`), and use managed identity for Postgres later if you want passwordless access.

---

## 8. Pre-launch checklist

**Repository delivery (complete; not deployment evidence)**
- [x] Private foundation and application templates compile
- [x] Preview-first OIDC release and schema-init ordering implemented
- [x] Worker shutdown, pre-release snapshots and fail-closed ingress gates tested offline
- [x] Opt-in daily snapshots and bounded, marked-snapshot-only retention implemented
- [x] Hashed runtime dependencies and dependency/image scan workflows added

**Security**
- [ ] Entra auth on web; API internal-only; unauthenticated calls rejected
- [x] Job caps, de-duplication and rate limits in place (repository tests; staging load test pending)
- [ ] No anonymous Grafana; telemetry points at Azure or is disabled
- [ ] Secrets in Key Vault; no passwords in env files or images
- [x] Image and dependency scan workflows in CI (successful scan evidence still required)

**Reliability**
- [ ] Worker min = max = 1, direct Postgres connection, job timeout added
- [ ] Azure Files mount tested for write access as UID 10001; FastF1 cache behaviour tested
- [ ] DB init job runs on deploy; Postgres backups enabled and a restore tested
- [x] Hashed lock file; model metadata includes library versions and loading checks them
- [ ] Image built for `linux/amd64`

**Model**
- [x] Whole-season results-only collector and time-based decay implemented (live collection remains pending)
- [ ] Window ablation done; final test locked and evaluated once
- [x] Paired comparison against the past-selected best baseline and top-10 temperature objective implemented (real evaluation remains pending)
- [ ] `evaluate` re-run and the output saved as the new reference
- [x] UI shows stage, history scope, calibration sample size, model version and training end date; missing evidence is explicit
- [x] Live scoreboard script and enqueue-only scheduler implemented (live forecasts and schedules remain pending)

**Legal and branding**
- [x] F1 font hotlink removed
- [ ] Data terms reviewed; disclaimer added; no F1 logos

**Operations**
- [ ] Budget alert, log retention and trace sampling set
- [ ] OTel verified from both gunicorn workers
- [ ] Rollback tested once in staging

---

## 9. Suggested order

1. **Week 1 (staging only):** A1-A7, deploy to a private staging environment, measure worker memory and CPU on one real forecast.
2. **Week 2:** Section 3 items 1-5 (data shape, ablation, paired comparison, locked test), then re-run `evaluate`.
3. **Week 3:** Section 3 items 6-8, section 6 and section 7. Invite a few users.
4. **After launch:** review the live scoreboard every few races before changing the model.

An honest first public version can be small: a read-only forecast page, per-race probabilities with uncertainty, a short limitations note and a live scoreboard. Add the job-queue UI for others only once the protections in A1 and A2 are proven.
