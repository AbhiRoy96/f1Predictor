"""Automatic semantic and PEP 440 versioning based on repository branch and git metadata."""

from __future__ import annotations

import dataclasses
from enum import Enum
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Optional


DEFAULT_BASE_VERSION = "0.2.0"
DEFAULT_MAINTENANCE_BASE_VERSION = "0.1.0"


class BranchCategory(str, Enum):
    """Classification of repository branches for release and development workflows."""
    MAIN = "main"                      # Trunk / current 'n' release branch
    MAINTENANCE = "maintenance"        # Long-term maintenance branch (e.g. n-1, maintenance/v0.1)
    RELEASE = "release"                # Release candidate branch (e.g. release/v0.2.0)
    DEV_FEATURE = "dev_feature"        # In-development feature branch (e.g. feature/*, dev-*)
    RESEARCH_FEATURE = "research"      # In-research experimental branch (e.g. experiment/*, research/*)
    OTHER = "other"                    # Other utility/historical branches


@dataclasses.dataclass(frozen=True)
class VersionInfo:
    """Structured representation of repository version metadata."""
    branch: str
    category: BranchCategory
    base_version: str
    semver: str
    pep440: str
    commit_sha: str
    commit_short: str
    commit_count: int
    is_dirty: bool
    branch_slug: str

    def to_dict(self) -> dict[str, Any]:
        """Convert version information to a standard dictionary."""
        return {
            "branch": self.branch,
            "category": self.category.value,
            "base_version": self.base_version,
            "semver": self.semver,
            "pep440": self.pep440,
            "commit_sha": self.commit_sha,
            "commit_short": self.commit_short,
            "commit_count": self.commit_count,
            "is_dirty": self.is_dirty,
            "branch_slug": self.branch_slug,
        }

    def to_json(self, indent: int = 2) -> str:
        """Format version metadata as JSON."""
        return json.dumps(self.to_dict(), indent=indent)


def slugify_branch_name(name: str) -> str:
    """Normalize a branch name for safe inclusion in version identifiers."""
    cleaned = re.sub(r"^(?:feature|feat|experiment|exp|research|dev|maintenance|maint|release)[/-]", "", name, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned).strip("-").lower()
    return slug or "branch"


def classify_branch(branch_name: str) -> BranchCategory:
    """Determine the branch category from its name."""
    normalized = (branch_name or "").strip().lower()

    if normalized in {"main", "master", "trunk"}:
        return BranchCategory.MAIN

    if normalized.startswith(("maintenance/", "maintenance-", "maint/", "maint-")):
        return BranchCategory.MAINTENANCE

    if normalized.startswith(("release/", "release-")):
        return BranchCategory.RELEASE

    if normalized.startswith(("experiment/", "experiment-", "research/", "research-")):
        return BranchCategory.RESEARCH_FEATURE

    if normalized.startswith(("feature/", "feature-", "feat/", "feat-", "dev-", "dev/")) or "dev" in normalized:
        return BranchCategory.DEV_FEATURE

    return BranchCategory.OTHER


def extract_base_version(branch_name: str, fallback_version: str = DEFAULT_BASE_VERSION) -> str:
    """Extract semantic version prefix if present in the branch name."""
    version_match = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", branch_name or "")
    if version_match:
        ver = version_match.group(1)
        parts = ver.split(".")
        while len(parts) < 3:
            parts.append("0")
        return ".".join(parts)

    category = classify_branch(branch_name)
    if category == BranchCategory.MAINTENANCE:
        return DEFAULT_MAINTENANCE_BASE_VERSION

    return fallback_version


def _run_git(args: list[str], repo_root: Optional[Path] = None) -> Optional[str]:
    """Execute a git command safely and return stripped output."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return proc.stdout.strip()
    except Exception:
        return None


def get_git_metadata(repo_root: Optional[Path] = None) -> dict[str, Any]:
    """Detect branch, commit SHA, commit count, and working-tree cleanliness."""
    # 1. Branch detection with CI fallback
    branch = (
        os.environ.get("GIT_BRANCH")
        or os.environ.get("GITHUB_HEAD_REF")
        or os.environ.get("GITHUB_REF_NAME")
        or _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
        or "main"
    )
    if branch == "HEAD":
        # Detached HEAD, attempt symbolic reference or tag
        tag = _run_git(["describe", "--tags", "--exact-match"], repo_root)
        branch = tag if tag else "detached"

    # 2. Commit SHA
    sha = (
        os.environ.get("GITHUB_SHA")
        or _run_git(["rev-parse", "HEAD"], repo_root)
        or "0000000000000000000000000000000000000000"
    )
    short_sha = sha[:7] if sha else "0000000"

    # 3. Commit count / distance
    count_str = _run_git(["rev-list", "--count", "HEAD"], repo_root)
    try:
        commit_count = int(count_str) if count_str else 0
    except ValueError:
        commit_count = 0

    # 4. Dirty state
    status = _run_git(["status", "--porcelain"], repo_root)
    is_dirty = bool(status and status.strip())

    return {
        "branch": branch,
        "commit_sha": sha,
        "commit_short": short_sha,
        "commit_count": commit_count,
        "is_dirty": is_dirty,
    }


def calculate_version(
    branch_name: Optional[str] = None,
    base_version: Optional[str] = None,
    repo_root: Optional[Path] = None,
    commit_sha: Optional[str] = None,
    commit_count: Optional[int] = None,
    is_dirty: Optional[bool] = None,
) -> VersionInfo:
    """Calculate SemVer 2.0.0 and PEP 440 versions dynamically for the given branch."""
    git_meta = get_git_metadata(repo_root)

    effective_branch = branch_name or git_meta["branch"]
    effective_sha = commit_sha or git_meta["commit_sha"]
    effective_short_sha = effective_sha[:7] if effective_sha else "0000000"
    effective_count = commit_count if commit_count is not None else git_meta["commit_count"]
    effective_dirty = is_dirty if is_dirty is not None else git_meta["is_dirty"]

    category = classify_branch(effective_branch)
    effective_base = base_version or extract_base_version(effective_branch)
    slug = slugify_branch_name(effective_branch)

    dirty_suffix = ".dirty" if effective_dirty else ""

    # Build SemVer and PEP440 strings based on category
    if category == BranchCategory.MAIN:
        # Production release trunk (n release)
        semver = f"{effective_base}"
        pep440 = f"{effective_base}"
        if effective_dirty:
            semver = f"{effective_base}+dirty"
            pep440 = f"{effective_base}+dirty"

    elif category == BranchCategory.MAINTENANCE:
        # Maintenance branch (n-1 release)
        semver = f"{effective_base}-maint.{effective_count}+{effective_short_sha}{dirty_suffix}"
        pep440 = f"{effective_base}.post{effective_count}+maint.{effective_short_sha}{dirty_suffix}"

    elif category == BranchCategory.RELEASE:
        # Release candidate branch
        semver = f"{effective_base}-rc.{effective_count}+{effective_short_sha}{dirty_suffix}"
        pep440 = f"{effective_base}rc{effective_count}+{effective_short_sha}{dirty_suffix}"

    elif category == BranchCategory.RESEARCH_FEATURE:
        # In-research feature branch (experimental)
        semver = f"{effective_base}-exp.{effective_count}+{slug}.{effective_short_sha}{dirty_suffix}"
        pep440 = f"{effective_base}.dev{effective_count}+exp.{slug}.{effective_short_sha}{dirty_suffix}"

    elif category == BranchCategory.DEV_FEATURE:
        # In-development feature branch
        semver = f"{effective_base}-dev.{effective_count}+{slug}.{effective_short_sha}{dirty_suffix}"
        pep440 = f"{effective_base}.dev{effective_count}+{slug}.{effective_short_sha}{dirty_suffix}"

    else:
        # Other utility or exploratory branch
        semver = f"{effective_base}-branch.{slug}.{effective_count}+{effective_short_sha}{dirty_suffix}"
        pep440 = f"{effective_base}.dev{effective_count}+{slug}.{effective_short_sha}{dirty_suffix}"

    return VersionInfo(
        branch=effective_branch,
        category=category,
        base_version=effective_base,
        semver=semver,
        pep440=pep440,
        commit_sha=effective_sha,
        commit_short=effective_short_sha,
        commit_count=effective_count,
        is_dirty=effective_dirty,
        branch_slug=slug,
    )


def get_version(
    style: str = "semver",
    branch_name: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> str:
    """Convenience helper to retrieve the version string directly."""
    info = calculate_version(branch_name=branch_name, repo_root=repo_root)
    if style.lower() in {"pep440", "python", "pip"}:
        return info.pep440
    if style.lower() in {"base", "short"}:
        return info.base_version
    return info.semver


def write_version_files(
    repo_root: Optional[Path] = None,
    info: Optional[VersionInfo] = None,
    update_package_json: bool = True,
) -> dict[str, Path]:
    """Persist generated version metadata into VERSION, version.json, and web/package.json."""
    root = repo_root or Path.cwd()
    version_info = info or calculate_version(repo_root=root)
    written: dict[str, Path] = {}

    # 1. VERSION plain text
    version_file = root / "VERSION"
    version_file.write_text(f"{version_info.semver}\n", encoding="utf-8")
    written["VERSION"] = version_file

    # 2. version.json structured
    json_file = root / "version.json"
    json_file.write_text(f"{version_info.to_json()}\n", encoding="utf-8")
    written["version.json"] = json_file

    # 3. web/package.json if present
    pkg_file = root / "web" / "package.json"
    if update_package_json and pkg_file.exists():
        try:
            pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
            pkg_data["version"] = version_info.semver.split("+")[0]  # npm requires standard semver without metadata in version
            pkg_file.write_text(json.dumps(pkg_data, indent=2) + "\n", encoding="utf-8")
            written["web/package.json"] = pkg_file
        except Exception:
            pass

    return written
