import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from main import main
from src.formats.latex.utils import is_already_downloaded
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
        with patch("src.runtime.batch_download_arxiv_tex", return_value=[]):
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
        with patch("src.runtime.batch_download_arxiv_tex") as download, patch("src.runtime.CoordinatorAgent"):
            result = run_translation(str(self.config), {"replace_paper_list": True}, project_items=[str(local)])
        download.assert_not_called()
        self.assertEqual(result["projects"], [str(local)])
