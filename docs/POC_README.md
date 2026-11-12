# Gridcast PoC: 100% Azure Container Apps (ACA) Deployment Guide

This document outlines the **Current State** and **Proposed PoC Architecture** for deploying Gridcast (Next.js frontend, Flask API, background simulation worker, schema migration job, and PostgreSQL 17 database) entirely within **Azure Container Apps (ACA)**.

---

### 1. Executive Summary & PoC Objective

The objective of this Proof of Concept (PoC) is to evaluate an all-in-one containerized deployment model on Azure Container Apps. By hosting all compute, API, worker, and database services inside a single ACA Managed Environment, the platform maximizes resource efficiency, eliminates baseline PaaS infrastructure costs (such as dedicated PostgreSQL Flexible Server instances), simplifies networking, and provides an ephemeral, rapidly reproducible deployment for testing and validation.

---

### 2. Architecture Comparison

#### Current State (Multi-Service Azure PaaS)
- **Frontend / API / Worker:** Hosted on Azure Container Apps with custom VNet integration.
- **Database:** Hosted on `Azure Database for PostgreSQL Flexible Server` with dedicated subnet delegation and private DNS zones.
- **Secrets:** Stored in `Azure Key Vault` and referenced via system-assigned managed identities.
- **Storage:** Dedicated `Azure Storage Account` with 3 Azure Files SMB shares mounted exclusively to the worker.
- **Networking:** Multi-subnet Azure Virtual Network with network security groups (NSGs) and routing rules.
- **Operational Cost:** Fixed baseline cost (~$40–$100+/month) due to PostgreSQL Flexible Server compute/storage and VNet infrastructure, regardless of workload activity.

```
+---------------------------------------------------------------------------------+
|                       Current Azure Architecture (Multi-PaaS)                   |
|                                                                                 |
|   Internet ---> [External Ingress]                                              |
|                       |                                                         |
|                       v                                                         |
|       +------------------------------------+                                    |
|       |  ACA Environment (Subnet A)        |                                    |
|       |   - gridcast-web                   |                                    |
|       |   - gridcast-api (Internal)        |                                    |
|       |   - gridcast-worker (No ingress)   |                                    |
|       +------------------------------------+                                    |
|              |                    |                                             |
|              v                    v                                             |
|   +--------------------+   +-----------------------+   +--------------------+   |
|   | Azure Postgres     |   | Azure Storage Account |   | Azure Key Vault    |   |
|   | Flexible Server    |   | (Azure Files SMB)     |   | (RBAC Secrets)     |   |
|   | (Delegated Subnet) |   | - data, models, rpts  |   |                    |   |
|   +--------------------+   +-----------------------+   +--------------------+   |
+---------------------------------------------------------------------------------+
```

---

#### Proposed State (100% Pure ACA Environment)
- **All Services in ACA:** Web, API, Worker, Database, and Migration Jobs reside inside a single ACA Managed Environment.
- **Database Container:** PostgreSQL 17 runs as a container app with internal TCP ingress on port 5432, strictly constrained to `minReplicas: 1, maxReplicas: 1`.
- **Integrated Storage:** Persistent data is attached via ACA environment-level volume mounts (Azure Files or local container volumes).
- **Native Secrets:** App and environment secrets are configured natively within ACA (`secretRef`), eliminating external Key Vault configuration for the PoC.
- **Internal Service Discovery:** All inter-service communications leverage ACA internal DNS (`gridcast-api:5000`, `gridcast-postgres:5432`).
- **Operational Cost:** Near-zero idle cost when scaled down, sharing a single unified Consumption plan.

```
+---------------------------------------------------------------------------------+
|                   Proposed PoC Architecture (100% ACA Only)                     |
|                                                                                 |
|   Internet ---> [External Ingress :443]                                         |
|                       |                                                         |
|                       v                                                         |
|       +------------------------------------+                                    |
|       | gridcast-web                       | (Next.js UI + ACA Entra Auth)      |
|       +------------------------------------+                                    |
|                       |                                                         |
|                       | Internal HTTP :5000                                     |
|                       v                                                         |
|       +------------------------------------+                                    |
|       | gridcast-api                       | <---> [gridcast-worker]             |
|       | (Flask REST API)                   |       (1 Replica, Simulation Job)  |
|       +------------------------------------+                                    |
|                       |                                      |                  |
|                       | Internal TCP :5432                   | TCP :5432        |
|                       +------------------+-------------------+                  |
|                                          |                                      |
|                                          v                                      |
|                       +------------------------------------+                    |
|                       | gridcast-postgres                  |                    |
|                       | (PostgreSQL 17 Container App)      |                    |
|                       +------------------------------------+                    |
|                                          ^                                      |
|                                          |                                      |
|                       +------------------------------------+                    |
|                       | gridcast-db-init                   |                    |
|                       | (One-off Schema Job)               |                    |
|                       +------------------------------------+                    |
+---------------------------------------------------------------------------------+
```

---

### 3. Detailed Component Comparison

| Component | Current State | Proposed PoC State (ACA Only) | Bare Minimum PoC | Primary Benefit / Trade-off |
| :--- | :--- | :--- | :--- | :--- |
| **Frontend UI** | ACA Container App (`gridcast-web`) | ACA Container App (`gridcast-web`) | ACA Container App (Public ingress, No Entra Auth) | Immediate accessibility without Entra app registration overhead. |
| **Backend API** | ACA Container App (`gridcast-api`) | ACA Container App (`gridcast-api`) | ACA Container App (Internal ingress `:5000`) | Internal DNS resolution (`gridcast-api:5000`). |
| **Database** | Azure Postgres Flexible Server (PaaS) | ACA Container App (`gridcast-postgres`) | ACA Container App (`postgres:17-alpine`) | Zero PaaS fixed costs; mounts Azure File share. |
| **Simulation Worker**| ACA Container App (`gridcast-worker`) | ACA Container App (`gridcast-worker`) | ACA Container App (1 replica, no ingress) | Processes simulations with PostgreSQL advisory locks. |
| **Schema Init** | ACA Job (Manual trigger) | ACA Job (Manual trigger) | ACA Job (`flask db-init`) | One-off execution before routing traffic. |
| **Secrets Management** | Azure Key Vault + User Managed Identity | ACA Native Secret Store (`secretRef`) | ACA Native Secret Store (`--secrets`) | Zero Key Vault provisioning; credentials stored directly in ACA. |
| **Identity / Auth** | Microsoft Entra ID (EasyAuth) | ACA Built-in Auth | **Disabled (Direct Access)** | No tenant app registration or callback redirects required. |
| **Networking** | Custom VNet, Subnet Delegation, NSGs | Default ACA Managed Environment Network | Default ACA Managed Environment Network | Zero custom VNet orchestration needed. |
| **Storage** | 3 Azure Files shares via Terraform | Azure Files share mounted in ACA environment | 1 Azure Files share on Storage Account | Single storage account for PostgreSQL data persistence. |

---

### 4. Bare Minimum PoC: ACA, ACR & Storage Account Only

To maximize deployment velocity, minimize resource overhead, and eliminate external dependencies, this **bare minimum configuration** provisions strictly three Azure resource types:

1. **Azure Container Registry (ACR - Basic SKU):** Container image build and storage.
2. **Azure Storage Account (Standard_LRS):** Single Azure File share for PostgreSQL data persistence (`/var/lib/postgresql/data`).
3. **Azure Container Apps (ACA) Managed Environment:** Single shared environment hosting all five compute units:
   - `gridcast-postgres` (PostgreSQL 17 on internal TCP port `5432`)
   - `gridcast-db-init` (One-off migration Job)
   - `gridcast-api` (Flask REST API on internal HTTP port `5000`)
   - `gridcast-worker` (Background simulation runner)
   - `gridcast-web` (Next.js dashboard on external HTTP port `3000`)

#### Excluded Services in Bare Minimum Setup
- ❌ **No Microsoft Entra ID / EasyAuth:** External ingress routes directly to Next.js without identity provider checks or tenant app registrations.
- ❌ **No Azure Database for PostgreSQL Flexible Server:** Database runs as a lightweight container app inside ACA.
- ❌ **No Azure Key Vault:** Secrets (`DATABASE_URL`, passwords) are passed directly into ACA environment/app secret configurations.
- ❌ **No Virtual Networks (VNets), Subnets, or Private DNS Zones:** Uses default ACA managed environment internal DNS for service discovery (`gridcast-api:5000`, `gridcast-postgres:5432`).

---

#### Bare Minimum All-in-One Deployment Script

Run this complete, self-contained Bash script in Azure CLI:

```bash
#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration Variables
# -----------------------------------------------------------------------------
LOCATION="eastus"
RANDOM_SUFFIX=$(( RANDOM % 9000 + 1000 ))
RG="gridcast-minpoc-rg-${RANDOM_SUFFIX}"
ENV_NAME="gridcast-minpoc-env"
ACR_NAME="gridcastminacr${RANDOM_SUFFIX}"
STORAGE_ACCOUNT="gridcastminstorage${RANDOM_SUFFIX}"
SHARE_NAME="postgres-data"
STORAGE_MOUNT_NAME="postgres-storage"
DB_PASSWORD="MinPocPassword${RANDOM_SUFFIX}!"

echo "=== [1/6] Creating Resource Group, ACR, and Storage Account ==="
az group create --name "$RG" --location "$LOCATION" -o none

# 1. ACR (Basic SKU)
az acr create --resource-group "$RG" --name "$ACR_NAME" --sku Basic --admin-enabled true -o none
ACR_SERVER="${ACR_NAME}.azurecr.io"
ACR_PASS=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)

# 2. Storage Account & File Share for PostgreSQL
az storage account create \
  --resource-group "$RG" \
  --name "$STORAGE_ACCOUNT" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --allow-blob-public-access false -o none

STORAGE_KEY=$(az storage account keys list --resource-group "$RG" --account-name "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)
az storage share create --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" --name "$SHARE_NAME" -o none

echo "=== [2/6] Building and Pushing Container Images ==="
az acr build --registry "$ACR_NAME" --image gridcast-api:latest .
az acr build --registry "$ACR_NAME" --image gridcast-web:latest ./web

echo "=== [3/6] Creating ACA Managed Environment & Linking Storage ==="
az containerapp env create --name "$ENV_NAME" --resource-group "$RG" --location "$LOCATION" -o none

# Mount Azure File share to ACA Environment
az containerapp env storage set \
  --name "$ENV_NAME" \
  --resource-group "$RG" \
  --storage-name "$STORAGE_MOUNT_NAME" \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$SHARE_NAME" \
  --access-mode ReadWrite -o none

echo "=== [4/6] Deploying PostgreSQL Database Container ==="
cat <<EOF > /tmp/min_postgres.yaml
properties:
  environmentId: $(az containerapp env show --name "$ENV_NAME" --resource-group "$RG" --query id -o tsv)
  configuration:
    secrets:
      - name: db-password
        value: "${DB_PASSWORD}"
    ingress:
      external: false
      targetPort: 5432
      transport: tcp
  template:
    containers:
      - name: postgres
        image: postgres:17-alpine
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: POSTGRES_DB
            value: gridcast
          - name: POSTGRES_USER
            value: gridcast
          - name: POSTGRES_PASSWORD
            secretRef: db-password
          - name: PGDATA
            value: /var/lib/postgresql/data/pgdata
        volumeMounts:
          - volumeName: postgres-vol
            mountPath: /var/lib/postgresql/data
    volumes:
      - name: postgres-vol
        storageType: AzureFile
        storageName: ${STORAGE_MOUNT_NAME}
        mountOptions: "uid=999,gid=999,dir_mode=0700,file_mode=0600"
    scale:
      minReplicas: 1
      maxReplicas: 1
EOF

az containerapp create --resource-group "$RG" --name gridcast-postgres --yaml /tmp/min_postgres.yaml -o none
rm -f /tmp/min_postgres.yaml

# Wait briefly for PostgreSQL port availability
sleep 15

echo "=== [5/6] Initializing Database Schema (ACA Job) ==="
DATABASE_URL="postgresql+psycopg://gridcast:${DB_PASSWORD}@gridcast-postgres:5432/gridcast"

az containerapp job create \
  --resource-group "$RG" \
  --name gridcast-db-init \
  --environment "$ENV_NAME" \
  --trigger-type Manual \
  --replica-timeout 600 \
  --replica-retry-limit 1 \
  --image "${ACR_SERVER}/gridcast-api:latest" \
  --registry-server "$ACR_SERVER" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASS" \
  --cpu 0.5 --memory 1.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url \
  --command "flask" --args "--app" "backend.app:create_app" "db-init" -o none

az containerapp job start --resource-group "$RG" --name gridcast-db-init -o none

echo "=== [6/6] Deploying API, Worker, and Web Frontend ==="

# 1. API (Internal Ingress)
az containerapp create \
  --resource-group "$RG" \
  --name gridcast-api \
  --environment "$ENV_NAME" \
  --image "${ACR_SERVER}/gridcast-api:latest" \
  --registry-server "$ACR_SERVER" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASS" \
  --ingress internal \
  --target-port 5000 \
  --min-replicas 1 \
  --max-replicas 2 \
  --cpu 0.5 --memory 1.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url OTEL_ENABLED=false -o none

# 2. Worker (No Ingress, 1 Replica)
az containerapp create \
  --resource-group "$RG" \
  --name gridcast-worker \
  --environment "$ENV_NAME" \
  --image "${ACR_SERVER}/gridcast-api:latest" \
  --registry-server "$ACR_SERVER" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASS" \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 1.0 --memory 2.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url OTEL_ENABLED=false \
  --command "python" --args "-m" "backend.worker" -o none

# 3. Web UI (External Ingress, Direct Access without Entra Auth)
az containerapp create \
  --resource-group "$RG" \
  --name gridcast-web \
  --environment "$ENV_NAME" \
  --image "${ACR_SERVER}/gridcast-web:latest" \
  --registry-server "$ACR_SERVER" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASS" \
  --ingress external \
  --target-port 3000 \
  --min-replicas 1 \
  --max-replicas 2 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars BACKEND_URL=http://gridcast-api:5000 -o none

WEB_URL=$(az containerapp show --resource-group "$RG" --name gridcast-web --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "============================================================================="
echo " Minimum PoC Deployed Successfully!"
echo " Web Application URL: https://${WEB_URL}"
echo " Resource Group:      ${RG}"
echo "============================================================================="
```

---

### 5. Detailed Step-by-Step PoC Deployment Instructions

#### Prerequisites
- Azure CLI installed and authenticated (`az login`).
- Azure Subscription with permissions to create Resource Groups and Container Apps.
- Docker or Azure Container Registry (`ACR`) for building AMD64 container images.

#### Step 1: Environment Setup
```bash
# Variables
RG="gridcast-poc-rg"
LOCATION="eastus"
ENV_NAME="gridcast-poc-env"
ACR_NAME="gridcastpocacr$RANDOM"
STORAGE_ACCOUNT="gridcastpocstorage$RANDOM"
SHARE_NAME="gridcast-db-data"

# Create Resource Group and ACR
az group create --name $RG --location $LOCATION
az acr create --resource-group $RG --name $ACR_NAME --sku Basic --admin-enabled true

# Build Container Images
az acr build --registry $ACR_NAME --image gridcast-api:latest .
az acr build --registry $ACR_NAME --image gridcast-web:latest ./web

# Create ACA Environment
az containerapp env create --name $ENV_NAME --resource-group $RG --location $LOCATION
```

#### Step 2: Configure Storage for Persistent Database
```bash
# Create Storage Account and File Share
az storage account create --resource-group $RG --name $STORAGE_ACCOUNT --location $LOCATION --sku Standard_LRS
STORAGE_KEY=$(az storage account keys list --resource-group $RG --account-name $STORAGE_ACCOUNT --query "[0].value" -o tsv)
az storage share create --account-name $STORAGE_ACCOUNT --account-key "$STORAGE_KEY" --name $SHARE_NAME

# Link File Share to ACA Environment
az containerapp env storage set \
  --name $ENV_NAME \
  --resource-group $RG \
  --storage-name postgres-storage \
  --azure-file-account-name $STORAGE_ACCOUNT \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name $SHARE_NAME \
  --access-mode ReadWrite
```

#### Step 3: Deploy PostgreSQL Database Container
Create `postgres.yaml`:
```yaml
properties:
  environmentId: /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/gridcast-poc-rg/providers/Microsoft.App/managedEnvironments/gridcast-poc-env
  configuration:
    secrets:
      - name: db-password
        value: "GridcastPocSecurePassword2026!"
    ingress:
      external: false
      targetPort: 5432
      transport: tcp
  template:
    containers:
      - name: postgres
        image: postgres:17-alpine
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: POSTGRES_DB
            value: gridcast
          - name: POSTGRES_USER
            value: gridcast
          - name: POSTGRES_PASSWORD
            secretRef: db-password
          - name: PGDATA
            value: /var/lib/postgresql/data/pgdata
        volumeMounts:
          - volumeName: postgres-vol
            mountPath: /var/lib/postgresql/data
    volumes:
      - name: postgres-vol
        storageType: AzureFile
        storageName: postgres-storage
        mountOptions: "uid=999,gid=999,dir_mode=0700,file_mode=0600"
    scale:
      minReplicas: 1
      maxReplicas: 1
```

Deploy the database:
```bash
az containerapp create --resource-group $RG --name gridcast-postgres --yaml postgres.yaml
```

#### Step 4: Run Schema Initialization Job
```bash
ACR_SERVER="$ACR_NAME.azurecr.io"
ACR_PASS=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

DATABASE_URL="postgresql+psycopg://gridcast:GridcastPocSecurePassword2026!@gridcast-postgres:5432/gridcast"

az containerapp job create \
  --resource-group $RG \
  --name gridcast-db-init \
  --environment $ENV_NAME \
  --trigger-type Manual \
  --replica-timeout 600 \
  --replica-retry-limit 1 \
  --image "$ACR_SERVER/gridcast-api:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASS" \
  --cpu 0.5 --memory 1.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url \
  --command "flask" --args "--app" "backend.app:create_app" "db-init"

# Execute migration
az containerapp job start --resource-group $RG --name gridcast-db-init
```

#### Step 5: Deploy Backend API & Background Worker
```bash
# Deploy Backend API (Internal Ingress)
az containerapp create \
  --resource-group $RG \
  --name gridcast-api \
  --environment $ENV_NAME \
  --image "$ACR_SERVER/gridcast-api:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASS" \
  --ingress internal \
  --target-port 5000 \
  --min-replicas 1 \
  --max-replicas 2 \
  --cpu 0.5 --memory 1.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url OTEL_ENABLED=false

# Deploy Worker (No Ingress, 1 Replica)
az containerapp create \
  --resource-group $RG \
  --name gridcast-worker \
  --environment $ENV_NAME \
  --image "$ACR_SERVER/gridcast-api:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASS" \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 1.0 --memory 2.0Gi \
  --secrets database-url="$DATABASE_URL" \
  --env-vars DATABASE_URL=secretref:database-url OTEL_ENABLED=false \
  --command "python" --args "-m" "backend.worker"
```

#### Step 6: Deploy Frontend Web Application
```bash
az containerapp create \
  --resource-group $RG \
  --name gridcast-web \
  --environment $ENV_NAME \
  --image "$ACR_SERVER/gridcast-web:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASS" \
  --ingress external \
  --target-port 3000 \
  --min-replicas 1 \
  --max-replicas 2 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars BACKEND_URL=http://gridcast-api:5000
```

---

### 6. Utility & Trade-Off Analysis

#### Utility Maximization
- **Cost Reduction:** Eliminates all PaaS fixed costs. The entire PoC runs on ACA Consumption pricing.
- **Operational Speed:** One script or pipeline can deploy and tear down the complete environment in under 10 minutes.
- **Architectural Cohesion:** Single pane of glass for logs, revisions, scaling, and traffic management under `Microsoft.App`.

#### Risks and Mitigations for PoC vs. Production
- **Database Durability & Availability:**
  - *Risk:* ACA container restarts will produce temporary database connection drops.
  - *Mitigation for PoC:* The API and Worker implement automatic database reconnection loops. Azure Files volume persists data across container restarts.
- **Storage Latency on SMB:**
  - *Risk:* PostgreSQL I/O performance over Azure Files SMB is slower than native managed SSDs.
  - *Mitigation for PoC:* Sufficient for PoC analytical query volumes; use Azure Files Premium tier if write I/O becomes a bottleneck.
- **Backup & Recovery:**
  - *Mitigation for PoC:* Create an ACA scheduled job to run `pg_dump` to an Azure Blob container or take Azure Files snapshots.

---

### 7. PoC Validation Checklist

- [ ] `gridcast-postgres` is running with `1/1` replicas and listening on internal TCP port `5432`.
- [ ] `gridcast-db-init` successfully executes and creates all schema tables.
- [ ] `gridcast-api` passes health checks on internal endpoint `http://gridcast-api:5000/api/health`.
- [ ] `gridcast-worker` successfully acquires the PostgreSQL session advisory lock (`pg_try_advisory_lock(716421)`).
- [ ] `gridcast-web` is accessible over HTTPS at its external FQDN and renders the Gridcast dashboard.
- [ ] A sample prediction job submitted through the UI is queued in PostgreSQL, processed by `gridcast-worker`, and stored back in the database.
