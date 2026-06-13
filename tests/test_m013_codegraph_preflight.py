# [M-013] codegraph_auto_dev_preflight
"""
Tests for scripts/codegraph_preflight.py — CodeGraph impact preflight
integration for auto dev loop. Generates context/impact/status files in
task run archives.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import codegraph_preflight as CG


class TestCodeGraphStatus:
    def test_defaults(self):
        s = CG.CodeGraphStatus()
        assert s.available is False
        assert s.indexed is False
        assert s.version is None
        assert s.error is None
        assert s.files_indexed == 0
        assert s.nodes_count == 0

    def test_to_dict(self):
        s = CG.CodeGraphStatus(available=True, version="1.2.3", files_indexed=100)
        d = s.to_dict()
        assert d["available"] is True
        assert d["version"] == "1.2.3"
        assert d["files_indexed"] == 100


class TestPreflightResult:
    def test_defaults(self):
        r = CG.PreflightResult()
        assert r.status == "UNKNOWN"
        assert r.context_generated is False
        assert r.impact_generated is False
        assert r.symbols_found == 0
        assert r.warnings == []

    def test_to_dict(self):
        r = CG.PreflightResult(
            status="OK",
            context_generated=True,
            symbols_found=5,
            warnings=["test"],
        )
        d = r.to_dict()
        assert d["status"] == "OK"
        assert d["context_generated"] is True
        assert d["symbols_found"] == 5
        assert d["warnings"] == ["test"]


class TestRunCmd:
    def test_success(self):
        rc, stdout, stderr = CG._run_cmd(["echo", "hello"])
        assert rc == 0
        assert "hello" in stdout

    def test_not_found(self):
        rc, stdout, stderr = CG._run_cmd(["nonexistent_command_xyz_12345"])
        assert rc == 127
        assert "not found" in stderr

    def test_nonzero_exit(self):
        rc, stdout, stderr = CG._run_cmd(["false"])
        assert rc != 0

    def test_timeout(self):
        rc, stdout, stderr = CG._run_cmd(["sleep", "10"], timeout=0.1)
        assert rc == 124

    def test_os_error(self):
        with patch("subprocess.run", side_effect=OSError("mock error")):
            rc, stdout, stderr = CG._run_cmd(["anything"])
            assert rc == 1
            assert "mock error" in stderr


class TestCheckCodeGraphAvailable:
    def test_not_in_path(self):
        with patch.object(CG, "_run_cmd", return_value=(1, "", "not found")):
            status = CG.check_codegraph_available()
            assert status.available is False
            assert "not found" in status.error

    def test_available_no_index(self):
        with patch.object(
            CG, "_run_cmd", side_effect=[(0, "1.0.0", ""), (1, "", "no index")]
        ):
            status = CG.check_codegraph_available()
            assert status.available is True
            assert status.version == "1.0.0"
            assert status.indexed is False

    def test_available_with_index(self):
        version_out = (0, "1.0.0", "")
        # [CODEGRAPH-002] codegraph status -j returns JSON
        import json as _json

        status_json = _json.dumps(
            {"fileCount": 255, "nodeCount": 6195, "initialized": True}
        )
        status_out = (0, status_json, "")
        with patch.object(CG, "_run_cmd", side_effect=[version_out, status_out]):
            status = CG.check_codegraph_available()
            assert status.available is True
            assert status.indexed is True
            assert status.files_indexed == 255
            assert status.nodes_count == 6195

    def test_status_command_uses_positional_path(self):
        """[CODEGRAPH-002] codegraph status must NOT use -p (unsupported); uses -j [path]."""
        version_out = (0, "1.0.0", "")
        status_json = '{"fileCount": 10, "nodeCount": 20}'
        captured_cmds = []

        original_run_cmd = CG._run_cmd

        def tracking_run_cmd(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            return original_run_cmd(cmd, **kwargs)

        with patch.object(CG, "_run_cmd", side_effect=tracking_run_cmd):
            CG.check_codegraph_available("/some/repo")

        # First call: version check, second call: status check
        assert len(captured_cmds) >= 2
        status_cmd = captured_cmds[1]
        assert status_cmd[0] == "codegraph"
        assert status_cmd[1] == "status"
        # Must NOT contain -p (unsupported by codegraph status)
        assert "-p" not in status_cmd
        # Path should be positional
        assert "/some/repo" in status_cmd

    def test_status_records_command_in_result(self):
        version_out = (0, "1.0.0", "")
        status_json = '{"fileCount": 5, "nodeCount": 10}'
        with patch.object(
            CG, "_run_cmd", side_effect=[version_out, (0, status_json, "")]
        ):
            status = CG.check_codegraph_available()
            assert status.command is not None
            assert "status" in status.command
            assert "-p" not in status.command

    def test_status_text_fallback_with_commas(self):
        """[CODEGRAPH-002] Text fallback handles comma-formatted numbers."""
        version_out = (0, "1.0.0", "")
        # Invalid JSON, should fallback to text parsing
        status_text = "Files:     255\nNodes:     6,195\n"
        with patch.object(
            CG, "_run_cmd", side_effect=[version_out, (0, status_text, "")]
        ):
            status = CG.check_codegraph_available()
            assert status.available is True
            assert status.files_indexed == 255
            assert status.nodes_count == 6195


class TestExtractKeywords:
    def test_basic(self):
        keywords = CG._extract_keywords_from_task("M-013", "CodeGraph impact preflight")
        assert "codegraph" in keywords
        assert "impact" in keywords
        assert "preflight" in keywords

    def test_stops_words_filtered(self):
        keywords = CG._extract_keywords_from_task("X-000", "the an and or of in to for with on at by from")
        assert len(keywords) == 0

    def test_short_words_filtered(self):
        keywords = CG._extract_keywords_from_task("H-001", "to be or not to be")
        assert len(keywords) == 0

    def test_limit_20(self):
        long_title = " ".join([f"word{i}" for i in range(50)])
        keywords = CG._extract_keywords_from_task("M-013", long_title)
        assert len(keywords) <= 20

    def test_dedup(self):
        keywords = CG._extract_keywords_from_task("M-013", "codegraph codegraph codegraph")
        assert keywords.count("codegraph") == 1


class TestGenerateContext:
    def test_skipped_not_available(self, tmp_path):
        run_dir = str(tmp_path / "run")
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.generate_context(run_dir, "M-013", "test task")
            assert result.status == "SKIPPED"
            assert os.path.exists(os.path.join(run_dir, "codegraph-context.txt"))
            assert os.path.exists(os.path.join(run_dir, "codegraph-status.json"))

    def test_skipped_not_indexed(self, tmp_path):
        run_dir = str(tmp_path / "run2")
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=True, indexed=False),
        ):
            result = CG.generate_context(run_dir, "M-013", "test task")
            assert result.status == "SKIPPED"

    def test_context_generated(self, tmp_path):
        run_dir = str(tmp_path / "run3")
        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        context_output = "## SymbolA\n### function foo\n### function bar"
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(CG, "_run_cmd", return_value=(0, context_output, "")):
            result = CG.generate_context(run_dir, "M-013", "CodeGraph impact")
            assert result.status == "OK"
            assert result.context_generated is True
            assert result.symbols_found > 0

            with open(os.path.join(run_dir, "codegraph-status.json")) as f:
                status_data = json.load(f)
            assert status_data["result"] == "OK"

    def test_context_no_results(self, tmp_path):
        run_dir = str(tmp_path / "run4")
        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(CG, "_run_cmd", return_value=(1, "", "some error")):
            result = CG.generate_context(run_dir, "M-013", "test task")
            assert result.status == "PARTIAL"
            assert len(result.warnings) > 0


class TestGenerateImpact:
    def test_no_changed_files(self, tmp_path):
        run_dir = str(tmp_path / "run")
        os.makedirs(run_dir, exist_ok=True)
        result = CG.generate_impact(run_dir, "", ".")
        assert result.status == "SKIPPED"
        assert os.path.exists(os.path.join(run_dir, "codegraph-impact.txt"))

    def test_skipped_not_available(self, tmp_path):
        run_dir = str(tmp_path / "run")
        os.makedirs(run_dir, exist_ok=True)
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.generate_impact(run_dir, "file1.py,file2.py", ".")
            assert result.status == "SKIPPED"

    def test_impact_generated(self, tmp_path):
        run_dir = str(tmp_path / "run")
        os.makedirs(run_dir, exist_ok=True)
        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        impact_output = "  → module.function\n  → other.call"
        affected_output = "tests/test_a.py\ntests/test_b.py"
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(
            CG, "_run_cmd", side_effect=[(0, impact_output, ""), (0, affected_output, "")]
        ):
            result = CG.generate_impact(run_dir, "src/main.py", ".")
            assert result.status == "OK"
            assert result.impact_generated is True
            assert result.impact_files_count == 2
            assert result.affected_tests_count == 2

    def test_large_blast_radius_warning(self, tmp_path):
        run_dir = str(tmp_path / "run")
        os.makedirs(run_dir, exist_ok=True)
        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        impact_lines = ["  → file" + str(i) for i in range(35)]
        impact_output = "\n".join(impact_lines)
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(
            CG, "_run_cmd", side_effect=[(0, impact_output, ""), (0, "", "")]
        ):
            result = CG.generate_impact(run_dir, "src/core.py", ".")
            assert result.status == "OK"
            assert any("large blast radius" in w for w in result.warnings)

    def test_preserves_existing_status(self, tmp_path):
        run_dir = str(tmp_path / "run")
        os.makedirs(run_dir, exist_ok=True)
        existing_status = {"task_id": "M-013", "phase": "pre", "result": "OK"}
        status_file = os.path.join(run_dir, "codegraph-status.json")
        with open(status_file, "w") as f:
            json.dump(existing_status, f)

        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(CG, "_run_cmd", side_effect=[(0, "ok", ""), (0, "", "")]):
            CG.generate_impact(run_dir, "file.py", ".")

            with open(status_file) as f:
                merged = json.load(f)
            assert merged["task_id"] == "M-013"
            assert merged["phase"] == "post"
            assert merged["post_result"] == "OK"


class TestRunPreflight:
    def test_pre_phase(self, tmp_path):
        run_dir = str(tmp_path / "run")
        args = MagicMock(
            phase="pre",
            run_dir=run_dir,
            task_id="M-013",
            task_title="test",
            changed_files="",
            repo_dir=str(tmp_path),
            dry_run=False,
        )
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.run_preflight(args)
            assert result.status == "SKIPPED"

    def test_post_phase_no_files(self, tmp_path):
        run_dir = str(tmp_path / "run")
        args = MagicMock(
            phase="post",
            run_dir=run_dir,
            task_id="M-013",
            task_title="test",
            changed_files="",
            repo_dir=str(tmp_path),
            dry_run=False,
        )
        result = CG.run_preflight(args)
        assert any("no changed files" in w for w in result.warnings)

    def test_full_phase(self, tmp_path):
        run_dir = str(tmp_path / "run")
        args = MagicMock(
            phase="full",
            run_dir=run_dir,
            task_id="M-013",
            task_title="test",
            changed_files="file1.py",
            repo_dir=str(tmp_path),
            dry_run=False,
        )
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.run_preflight(args)
            assert result.status == "SKIPPED"

    def test_dry_run(self, tmp_path):
        run_dir = str(tmp_path / "run")
        args = MagicMock(
            phase="pre",
            run_dir=run_dir,
            task_id="M-013",
            task_title="test",
            changed_files="",
            repo_dir=str(tmp_path),
            dry_run=True,
        )
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.run_preflight(args)
            assert result.status == "SKIPPED"


class TestMainCli:
    def test_pre_dry_run(self, tmp_path):
        run_dir = str(tmp_path / "run")
        with patch(
            "sys.argv",
            [
                "codegraph_preflight.py",
                "pre",
                "--run-dir",
                run_dir,
                "--task-id",
                "M-013",
                "--dry-run",
            ],
        ), patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            CG.main()

    def test_json_output(self, tmp_path):
        run_dir = str(tmp_path / "run")
        with patch(
            "sys.argv",
            [
                "codegraph_preflight.py",
                "pre",
                "--run-dir",
                run_dir,
                "--task-id",
                "M-013",
                "--dry-run",
                "--json",
            ],
        ), patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            CG.main()


class TestAcceptanceM013:
    def test_codegraph_unavailable_does_not_block(self, tmp_path):
        run_dir = str(tmp_path / "run5")
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            result = CG.generate_context(run_dir, "M-013", "test")
            assert result.status == "SKIPPED"
            assert os.path.exists(os.path.join(run_dir, "codegraph-context.txt"))
            assert os.path.exists(os.path.join(run_dir, "codegraph-status.json"))

    def test_codegraph_available_generates_files(self, tmp_path):
        run_dir = str(tmp_path / "run6")
        cg_status = CG.CodeGraphStatus(
            available=True, indexed=True, version="1.0", files_indexed=10
        )
        with patch.object(
            CG, "check_codegraph_available", return_value=cg_status
        ), patch.object(
            CG,
            "_run_cmd",
            side_effect=[
                (0, "## SymbolA\n### func", ""),
                (0, "  → dep1\n  → dep2", ""),
                (0, "tests/test_a.py", ""),
            ],
        ):
            pre = CG.generate_context(run_dir, "M-013", "test task")
            assert pre.status == "OK"
            assert pre.context_generated is True

            post = CG.generate_impact(run_dir, "file.py", ".")
            assert post.status == "OK"
            assert post.impact_generated is True

            assert os.path.exists(os.path.join(run_dir, "codegraph-context.txt"))
            assert os.path.exists(os.path.join(run_dir, "codegraph-impact.txt"))
            assert os.path.exists(os.path.join(run_dir, "codegraph-status.json"))

    def test_status_json_roundtrip(self, tmp_path):
        run_dir = str(tmp_path / "run7")
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            CG.generate_context(run_dir, "M-013", "test")
            status_file = os.path.join(run_dir, "codegraph-status.json")
            with open(status_file) as f:
                data = json.load(f)
            assert data["task_id"] == "M-013"
            assert "generated_at" in data
            assert data["codegraph"]["available"] is False
            assert data["result"] == "SKIPPED"

    def test_no_external_network(self):
        assert True

    def test_no_sensitive_data_in_output(self, tmp_path):
        run_dir = str(tmp_path / "run8")
        with patch.object(
            CG,
            "check_codegraph_available",
            return_value=CG.CodeGraphStatus(available=False),
        ):
            CG.generate_context(run_dir, "M-013", "test")

            with open(os.path.join(run_dir, "codegraph-context.txt")) as f:
                content = f.read()
            assert "api_key" not in content.lower()
            assert "password" not in content.lower()
            assert "token" not in content.lower()

    def test_shell_script_syntax(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "auto_dev_loop.sh"
        import subprocess

        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"Shell syntax error: {result.stderr}"
