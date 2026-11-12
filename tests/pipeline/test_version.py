"""Unit and regression tests for automated branch versioning."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.utils.version import (
    BranchCategory,
    VersionInfo,
    calculate_version,
    classify_branch,
    extract_base_version,
    get_version,
    slugify_branch_name,
    write_version_files,
)
from scripts.version import main as version_cli_main
import main as app_main


class TestBranchClassification(unittest.TestCase):
    """Test branch classification according to repository branching strategy."""

    def test_classify_main_branches(self):
        self.assertEqual(classify_branch("main"), BranchCategory.MAIN)
        self.assertEqual(classify_branch("master"), BranchCategory.MAIN)
        self.assertEqual(classify_branch("trunk"), BranchCategory.MAIN)

    def test_classify_maintenance_branches(self):
        self.assertEqual(classify_branch("maintenance/v0.1"), BranchCategory.MAINTENANCE)
        self.assertEqual(classify_branch("maintenance/v0.1.x"), BranchCategory.MAINTENANCE)
        self.assertEqual(classify_branch("maint-v0.1"), BranchCategory.MAINTENANCE)
        self.assertEqual(classify_branch("maint/v0.1"), BranchCategory.MAINTENANCE)

    def test_classify_release_branches(self):
        self.assertEqual(classify_branch("release/v0.2.0"), BranchCategory.RELEASE)
        self.assertEqual(classify_branch("release/1.0.0"), BranchCategory.RELEASE)
        self.assertEqual(classify_branch("release-2026.1"), BranchCategory.RELEASE)

    def test_classify_research_branches(self):
        self.assertEqual(classify_branch("experiment/bayesian-xgb-ensemble"), BranchCategory.RESEARCH_FEATURE)
        self.assertEqual(classify_branch("experiment/dynamic-bayesian"), BranchCategory.RESEARCH_FEATURE)
        self.assertEqual(classify_branch("research/lap-time-degradation"), BranchCategory.RESEARCH_FEATURE)

    def test_classify_dev_feature_branches(self):
        self.assertEqual(classify_branch("feature/pit-stop-optimisation"), BranchCategory.DEV_FEATURE)
        self.assertEqual(classify_branch("feat/fastf1-upgrade"), BranchCategory.DEV_FEATURE)
        self.assertEqual(classify_branch("dev-integration-agent-v2"), BranchCategory.DEV_FEATURE)
        self.assertEqual(classify_branch("dev-fix-perfomance"), BranchCategory.DEV_FEATURE)
        self.assertEqual(classify_branch("predictor-dev"), BranchCategory.DEV_FEATURE)

    def test_classify_other_branches(self):
        self.assertEqual(classify_branch("patch-1"), BranchCategory.OTHER)
        self.assertEqual(classify_branch("pr-ready-f1-prediction"), BranchCategory.OTHER)


class TestVersionCalculation(unittest.TestCase):
    """Test calculation of SemVer and PEP 440 version identifiers."""

    def test_main_branch_version(self):
        info = calculate_version(
            branch_name="main",
            base_version="0.2.0",
            commit_sha="a1b2c3d4e5f6",
            commit_count=10,
            is_dirty=False,
        )
        self.assertEqual(info.category, BranchCategory.MAIN)
        self.assertEqual(info.semver, "0.2.0")
        self.assertEqual(info.pep440, "0.2.0")

    def test_main_branch_dirty_version(self):
        info = calculate_version(
            branch_name="main",
            base_version="0.2.0",
            commit_sha="a1b2c3d4e5f6",
            commit_count=10,
            is_dirty=True,
        )
        self.assertEqual(info.semver, "0.2.0+dirty")
        self.assertEqual(info.pep440, "0.2.0+dirty")

    def test_maintenance_branch_version(self):
        info = calculate_version(
            branch_name="maintenance/v0.1",
            commit_sha="e562874111223344",
            commit_count=15,
            is_dirty=False,
        )
        self.assertEqual(info.category, BranchCategory.MAINTENANCE)
        self.assertEqual(info.base_version, "0.1.0")
        self.assertEqual(info.semver, "0.1.0-maint.15+e562874")
        self.assertEqual(info.pep440, "0.1.0.post15+maint.e562874")

    def test_release_candidate_version(self):
        info = calculate_version(
            branch_name="release/v0.2.0",
            commit_sha="1234567890abcdef",
            commit_count=8,
            is_dirty=False,
        )
        self.assertEqual(info.category, BranchCategory.RELEASE)
        self.assertEqual(info.base_version, "0.2.0")
        self.assertEqual(info.semver, "0.2.0-rc.8+1234567")
        self.assertEqual(info.pep440, "0.2.0rc8+1234567")

    def test_in_development_feature_version(self):
        info = calculate_version(
            branch_name="feature/lap-time-model",
            base_version="0.2.0",
            commit_sha="fedcba0987654321",
            commit_count=5,
            is_dirty=False,
        )
        self.assertEqual(info.category, BranchCategory.DEV_FEATURE)
        self.assertEqual(info.semver, "0.2.0-dev.5+lap-time-model.fedcba0")
        self.assertEqual(info.pep440, "0.2.0.dev5+lap-time-model.fedcba0")

    def test_in_research_feature_version(self):
        info = calculate_version(
            branch_name="experiment/dynamic-bayesian",
            base_version="0.2.0",
            commit_sha="9876543210fedcba",
            commit_count=12,
            is_dirty=False,
        )
        self.assertEqual(info.category, BranchCategory.RESEARCH_FEATURE)
        self.assertEqual(info.semver, "0.2.0-exp.12+dynamic-bayesian.9876543")
        self.assertEqual(info.pep440, "0.2.0.dev12+exp.dynamic-bayesian.9876543")

    def test_slugify_branch_name(self):
        self.assertEqual(slugify_branch_name("feature/add_new_endpoint"), "add-new-endpoint")
        self.assertEqual(slugify_branch_name("experiment/bayesian-xgb-ensemble"), "bayesian-xgb-ensemble")
        self.assertEqual(slugify_branch_name("dev-integration-agent-v2"), "integration-agent-v2")


class TestVersionPersistenceAndCLI(unittest.TestCase):
    """Test version file writing and CLI commands."""

    def test_write_version_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            web_dir = tmp_root / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text('{"name": "test-web", "version": "0.1.0"}', encoding="utf-8")

            info = calculate_version(
                branch_name="release/v0.2.0",
                commit_sha="1122334455667788",
                commit_count=3,
                is_dirty=False,
                repo_root=tmp_root,
            )

            written = write_version_files(repo_root=tmp_root, info=info)
            self.assertIn("VERSION", written)
            self.assertIn("version.json", written)
            self.assertIn("web/package.json", written)

            version_text = (tmp_root / "VERSION").read_text(encoding="utf-8").strip()
            self.assertEqual(version_text, "0.2.0-rc.3+1122334")

            version_json = json.loads((tmp_root / "version.json").read_text(encoding="utf-8"))
            self.assertEqual(version_json["branch"], "release/v0.2.0")
            self.assertEqual(version_json["category"], "release")
            self.assertEqual(version_json["semver"], "0.2.0-rc.3+1122334")

            pkg_json = json.loads((web_dir / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(pkg_json["version"], "0.2.0-rc.3")

    def test_version_cli_and_main(self):
        with patch("sys.stdout") as mock_stdout:
            info = app_main.main(["version"])
            self.assertIsNotNone(info)
            self.assertIsInstance(info, VersionInfo)


if __name__ == "__main__":
    unittest.main()
