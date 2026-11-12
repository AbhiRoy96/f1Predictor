#!/usr/bin/env python3
"""Command-line utility for automated branch versioning and metadata management."""

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.version import (
    calculate_version,
    classify_branch,
    write_version_files,
    DEFAULT_BASE_VERSION,
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Calculate and manage dynamic project version based on git branch."
    )
    parser.add_argument(
        "--format",
        choices=["semver", "pep440", "json", "short", "all"],
        default="semver",
        help="Output format (default: semver)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Override current git branch name for testing or simulation",
    )
    parser.add_argument(
        "--base-version",
        type=str,
        default=None,
        help=f"Override base version (default: {DEFAULT_BASE_VERSION})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write version to VERSION, version.json, and web/package.json",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output the version string with no extra text",
    )

    args = parser.parse_args(argv)

    info = calculate_version(
        branch_name=args.branch,
        base_version=args.base_version,
        repo_root=REPO_ROOT,
    )

    if args.write:
        written = write_version_files(repo_root=REPO_ROOT, info=info)
        if not args.quiet:
            print("Successfully written version files:")
            for key, path in written.items():
                print(f"  - {key}: {path}")

    if args.format == "semver":
        print(info.semver)
    elif args.format == "pep440":
        print(info.pep440)
    elif args.format == "short":
        print(info.base_version)
    elif args.format == "json":
        print(info.to_json())
    elif args.format == "all":
        print(f"Branch:       {info.branch} ({info.category.value})")
        print(f"Base Version: {info.base_version}")
        print(f"SemVer:       {info.semver}")
        print(f"PEP 440:      {info.pep440}")
        print(f"Commit:       {info.commit_short} (count: {info.commit_count})")
        print(f"Dirty:        {info.is_dirty}")


if __name__ == "__main__":
    main()
