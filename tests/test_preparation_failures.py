import io
import tempfile
import unittest
import zipfile

import requests
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from main import main
from src.formats.latex.utils import download_tex, is_already_downloaded
from src.gui.streamlit_app import _retry_inputs
from src.runtime import run_translation


class PreparationFailureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sources = self.root / "sources"
        self.config = self.root / "config.toml"
        self.config.write_text(
            f'tex_sources_dir = "{self.sources.as_posix()}"\n'
            f'output_dir = "{(self.root / "out").as_posix()}"\n'
            'user_term = "local.csv"\n'
        )

    def test_mixed_cli_batch_counts_corrupt_archive_and_translates_remaining_project(self):
        valid = self.root / "valid"
        valid.mkdir()
        archive = self.root / "broken.zip"
        archive.write_bytes(b"corrupt")
        args = ["latextrans", "--config", str(self.config), "--project", f"{archive},{valid}"]
        with (
            patch("sys.argv", args),
            patch("src.runtime.CoordinatorAgent") as coordinator,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(coordinator.call_args.kwargs["project_dir"], str(valid))
        self.assertIn("Completed 1 project(s), failed 1.", output.getvalue())
        self.assertIn(str(archive), output.getvalue())
        self.assertFalse((self.sources / "broken").exists())

        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("main.tex", r"\documentclass{article}")
        with patch("src.runtime.CoordinatorAgent"):
            retry = run_translation(str(self.config), project_items=[str(archive)])
        self.assertEqual(retry["projects"], [str(self.sources / "broken")])
        self.assertFalse(retry["failed_projects"])

    def test_all_failed_inputs_keep_reasons_events_and_retry_types(self):
        missing = str(self.root / "missing")
        paper = "2501.12948"
        events = []
        with patch("src.runtime.prepare_arxiv_source", side_effect=ValueError("download failed")):
            result = run_translation(
                str(self.config), {"paper_list": [paper]}, project_items=[missing],
                event_callback=events.append,
            )
        self.assertEqual(result["completed_projects"], [])
        failed = result["failed_projects"]
        self.assertIn("download", failed[0]["error"])
        self.assertIn("Invalid local project", failed[1]["error"])
        self.assertEqual(_retry_inputs(failed), {"project_items": [missing], "paper_list": [paper]})
        self.assertEqual([(e["type"], e["index"], e["total"]) for e in events], [
            ("project_error", 1, 2), ("project_error", 2, 2),
        ])
        # A PDF downloaded without the TeX source must not prevent a later retry.
        cache = self.sources / paper
        cache.mkdir()
        (cache / f"{paper}.pdf").touch()
        self.assertFalse(is_already_downloaded(paper, self.sources))

    def test_all_existing_reports_unreadable_archive(self):
        self.sources.mkdir()
        archive = self.sources / "corrupt.tar.gz"
        archive.write_bytes(b"not a tarball")
        result = run_translation(str(self.config), all_existing=True)
        self.assertEqual(result["completed_projects"], [])
        self.assertEqual(result["failed_projects"][0]["project_dir"], str(archive))

    def test_retry_does_not_repeat_papers_from_config(self):
        with self.config.open("a") as config:
            config.write('paper_list = ["2501.12948"]\n')
        local = self.root / "repaired"
        local.mkdir()
        with patch("src.runtime.prepare_arxiv_source") as download, patch("src.runtime.CoordinatorAgent"):
            result = run_translation(str(self.config), {"replace_paper_list": True}, project_items=[str(local)])
        download.assert_not_called()
        self.assertEqual(result["projects"], [str(local)])

    def test_interrupted_download_does_not_publish_archive(self):
        response = MagicMock()
        def interrupted(_):
            yield b"partial bytes"
            raise requests.ConnectionError("connection lost")
        response.iter_content.side_effect = interrupted
        with patch("src.formats.latex.utils.requests.get") as get:
            get.return_value.__enter__.return_value = response
            self.assertIsNone(download_tex("hep-th/9901001", "unused", self.sources, {}))
        self.assertFalse(is_already_downloaded("hep-th/9901001", self.sources))
        self.assertEqual(list((self.sources / "hep-th").iterdir()), [])

    def test_legacy_id_retries_corrupt_cache_and_keeps_batch_running(self):
        paper = "hep-th/9901001"
        project = self.sources / paper
        project.mkdir(parents=True)
        (project / "partial.tex").touch()  # Legacy in-place extraction.
        archive = Path(f"{project}.tar.gz")
        archive.write_bytes(b"corrupt")
        local = self.root / "other"
        local.mkdir()
        with patch("src.runtime.CoordinatorAgent"):
            first = run_translation(str(self.config), {"paper_list": [paper]}, project_items=[str(local)])
        self.assertEqual(first["projects"], [str(local)])
        self.assertFalse(is_already_downloaded(paper, self.sources))
        self.assertFalse(archive.exists())

        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as source:
            source.writestr("main.tex", "fresh")
        with (
            patch("src.formats.latex.utils.get_tex_url", return_value="source"),
            patch("src.formats.latex.utils.requests.get") as get,
            patch("src.runtime.CoordinatorAgent"),
        ):
            get.return_value.__enter__.return_value.iter_content.return_value = [data.getvalue()]
            get.return_value.content = b"original pdf"
            retry = run_translation(str(self.config), _retry_inputs(first["failed_projects"]))
        self.assertFalse(retry["failed_projects"])
        self.assertEqual((project / "main.tex").read_text(), "fresh")
        self.assertFalse((project / "partial.tex").exists())
        self.assertEqual((project / "9901001.pdf").read_bytes(), b"original pdf")

    def test_two_all_existing_runs_count_top_level_inputs_and_keep_existing_projects(self):
        paper = self.sources / "paper"
        supplement = paper / "supplement"
        supplement.mkdir(parents=True)
        (supplement / "unused.zip").write_bytes(b"corrupt")
        (paper / "main.tex").write_text("original")
        archive = self.sources / "broken.zip"
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("first.tex", "partial")
            source.writestr("second.tex", "unique contents")
        archive.write_bytes(archive.read_bytes().replace(b"unique contents", b"wrong! contents"))
        with patch("src.runtime.CoordinatorAgent"):
            for _ in range(2):
                result = run_translation(str(self.config), all_existing=True)
                self.assertEqual(result["projects"], [str(paper)])
                self.assertEqual([f["project_dir"] for f in result["failed_projects"]], [str(archive)])
                self.assertFalse((self.sources / "broken").exists())
            archive.rename(self.sources / "paper.zip")
            run_translation(str(self.config), all_existing=True)
        self.assertEqual((paper / "main.tex").read_text(), "original")
        self.assertFalse((paper / "first.tex").exists())

    def test_workflow_retry_preserves_category_and_only_runs_failed_arxiv_id(self):
        failed, good = "2501.12948", "2501.12949"
        self.config.write_text(self.config.read_text().replace('user_term = "local.csv"',
                                                              f'paper_list = ["{failed}", "{good}"]'))
        for paper in (failed, good):
            project = self.sources / paper
            project.mkdir(parents=True)
            (project / "main.tex").touch()
        with (
            patch("src.runtime.CoordinatorAgent") as coordinator,
            patch("src.runtime.get_arxiv_category", side_effect=lambda ids: {ids[0]: ["cs.AI"]}) as categories,
        ):
            coordinator.return_value.workflow_latextrans.side_effect = [ValueError("compiler failed"), "ok", "ok"]
            first = run_translation(str(self.config))
            inputs = _retry_inputs(first["failed_projects"])
            self.assertEqual(inputs, {"paper_list": [failed], "project_items": [], "category": {failed: ["cs.AI"]}})
            categories.reset_mock()
            retried = run_translation(str(self.config), {**inputs, "replace_paper_list": True})
            categories.assert_not_called()
        self.assertEqual(retried["projects"], [str(self.sources / failed)])
        self.assertEqual(coordinator.call_args.kwargs["config"]["category"][failed], ["cs.AI"])
