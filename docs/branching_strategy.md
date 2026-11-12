---
title: Repository Branching Strategy and Automated Versioning
description: Specification of branch taxonomy, release workflows, maintenance lifecycles, and automated versioning for Gridcast.
---

# Branching Strategy & Automated Versioning

This document outlines the branching taxonomy, release lifecycles, and automated versioning system used across the Gridcast repository. This workflow ensures predictability, continuous integration safety, and seamless package metadata management across Python backend, machine learning models, and frontend applications.

---

## 1. Branch Taxonomy

The repository utilizes a structured branching model designed for parallel development, experimental research, release stabilization, and active maintenance of prior releases.

```
                  ┌───────────────┐
                  │ main (Trunk)  │ ──> Release n (Production)
                  └───┬───────────┘
                      │
     ┌────────────────┼────────────────┬────────────────┐
     ▼                ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ maintenance/ │ │   release/   │ │   feature/   │ │ experiment/  │
│  (e.g. v0.1) │ │ (e.g. v0.2.0)│ │  or dev-*    │ │ or research/ │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
  Maintenance      Release Hardening  In-Development   In-Research ML
  (n-1 line)       (Pre-release RCs)  Features         Exploration
```

### 1.1 Main Release Trunk (`main`)
- **Role**: Production branch and current release line `n`. Renamed from legacy `master`.
- **Target Audience**: Stable deployment artifacts, production Azure Container Apps releases, and documentation.
- **Base Version**: `0.2.0` (or latest semantic release tag).
- **Versioning Rule**: Direct semantic version string (e.g. `0.2.0` or `0.2.0+dirty` if local modifications exist).

### 1.2 Maintenance Branches (`maintenance/*` or `maint/*`)
- **Role**: Long-term support and critical bugfix backports for previous major/minor release lines (`n-1`, `n-2`).
- **Naming Convention**: `maintenance/v<major>.<minor>` (e.g. `maintenance/v0.1` for `n-1` maintenance).
- **Target Audience**: Production environments running prior release lines requiring security and critical operational fixes without adopting breaking changes.
- **Versioning Rule**: `X.Y.<patch>-maint.<commit_count>+<commit_sha>` (SemVer) and `X.Y.<patch>.post<commit_count>+maint.<commit_sha>` (PEP 440).

### 1.3 Release Branches (`release/*`)
- **Role**: Stabilization and validation for impending production releases (`n`).
- **Naming Convention**: `release/v<major>.<minor>.<patch>` (e.g. `release/v0.2.0`).
- **Target Audience**: Release candidates, integration smoke tests, staging validation, and pre-release audits.
- **Versioning Rule**: `X.Y.Z-rc.<commit_count>+<commit_sha>` (SemVer) and `X.Y.Zrc<commit_count>+<commit_sha>` (PEP 440).

### 1.4 In-Development Feature Branches (`feature/*`, `feat/*`, `dev/*`, `dev-*`)
- **Role**: Active software engineering, backend improvements, new pipeline steps, and infrastructure enhancements.
- **Naming Convention**: `feature/<feature-name>`, `feat/<name>`, or `dev-<descriptor>`.
- **Examples**: `feature/fastf1-upgrade`, `dev-integration-agent-v2`.
- **Versioning Rule**: `X.Y.Z-dev.<commit_count>+<slug>.<commit_sha>` (SemVer) and `X.Y.Z.dev<commit_count>+<slug>.<commit_sha>` (PEP 440).

### 1.5 In-Research Feature Branches (`experiment/*`, `research/*`)
- **Role**: Algorithmic exploration, probabilistic model tuning, feature engineering ablation studies, and ML research.
- **Naming Convention**: `experiment/<experiment-name>` or `research/<hypothesis>`.
- **Examples**: `experiment/bayesian-xgb-ensemble`, `experiment/dynamic-bayesian`.
- **Versioning Rule**: `X.Y.Z-exp.<commit_count>+<slug>.<commit_sha>` (SemVer) and `X.Y.Z.dev<commit_count>+exp.<slug>.<commit_sha>` (PEP 440).

### 1.6 Other Branches
- Utility, exploratory, and temporary branches (e.g. `predictor-dev`, `pr-ready-f1-prediction`) remain as-is and resolve to development version strings containing their sanitized branch slug.

---

## 2. Automated Versioning System

The versioning system is implemented in `src/utils/version.py` and exposed via CLI in `scripts/version.py` and `main.py`.

### 2.1 Dynamic Version Matrix

| Branch Category | Branch Example | SemVer 2.0.0 Format | PEP 440 Format (Python) |
| :--- | :--- | :--- | :--- |
| **Main (n)** | `main` | `0.2.0` | `0.2.0` |
| **Maintenance (n-1)** | `maintenance/v0.1` | `0.1.0-maint.27+8b9ba62` | `0.1.0.post27+maint.8b9ba62` |
| **Release Candidate** | `release/v0.2.0` | `0.2.0-rc.8+1234567` | `0.2.0rc8+1234567` |
| **In-Development** | `feature/pit-stop` | `0.2.0-dev.5+pit-stop.fedcba0` | `0.2.0.dev5+pit-stop.fedcba0` |
| **In-Research ML** | `experiment/dynamic-bayes`| `0.2.0-exp.12+dynamic-bayes.9876543`| `0.2.0.dev12+exp.dynamic-bayes.9876543`|

### 2.2 CLI Usage

#### Check Current Repository Version
```bash
python main.py version
# Output: Gridcast 0.2.0-dev.27+integration-agent-v2.8b9ba62 (dev-integration-agent-v2) [8b9ba62]
```

#### Detailed Version Breakdown
```bash
python scripts/version.py --format all
```

#### Write Version Files (`VERSION`, `version.json`, `web/package.json`)
```bash
python scripts/version.py --write
```

#### Simulate Branch Versioning
```bash
python scripts/version.py --branch maintenance/v0.1 --format semver
python scripts/version.py --branch release/v0.2.0 --format pep440
```

### 2.3 Programmatic API

```python
from src.utils.version import calculate_version, get_version

# Retrieve string
semver_str = get_version(style="semver")
pep440_str = get_version(style="pep440")

# Retrieve full structured metadata
info = calculate_version()
print(f"Branch: {info.branch}, Category: {info.category.value}, Commit: {info.commit_short}")
```

---

## 3. Workflow & Contribution Guidelines

1. **Developing Features**: Branch off `main` with `feature/<name>` or `dev-<name>`.
2. **Conducting ML Experiments**: Branch off `main` with `experiment/<model-name>` or `research/<hypothesis>`.
3. **Preparing Releases**: Cut `release/vX.Y.Z` from `main`, run full staging and validation suites, then merge back into `main` and tag the release commit.
4. **Maintaining Prior Releases (n-1)**: Apply critical fixes directly to `maintenance/v<major>.<minor>`, run tests, and tag maintenance patch releases (`v0.1.1`, `v0.1.2`).
