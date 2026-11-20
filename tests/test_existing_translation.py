import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import toml
from streamlit.testing.v1 import AppTest

from main import main
from src.agents import coordinator_agent
from src.runtime import run_translation
from src.utils.translation_output import _lock, translation_slot


class ExistingTranslationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.sources = root / "sources"
        self.paper = self.sources / "2410.05160"
        self.paper.mkdir(parents=True)
        self.output = root / "outputs"
        self.name = "ch_2410.05160"
        for name in (self.name, self.name + "_1", self.name + "_2"):
            (self.output / name).mkdir(parents=True)
        self.pdf = self.output / self.name / (self.name + ".pdf")
        self.pdf.write_bytes(b"previous")
        self.config = root / "config.toml"
        self.config.write_text(toml.dumps({"output_dir": str(self.output), "tex_sources_dir": str(self.sources)}))
        self.request = dict(config_path=str(self.config), project_items=[str(self.paper)])
        parser = self.enter_patch(coordinator_agent, "ParserAgent")
        self.parse = parser.return_value.execute
        self.enter_patch(coordinator_agent, "TranslatorAgent").return_value.execute = AsyncMock()
        self.enter_patch(coordinator_agent, "ValidatorAgent").return_value.execute.return_value = []
        self.generator = self.enter_patch(coordinator_agent, "GeneratorAgent").return_value
        produced = root / "translated.pdf"
        produced.write_bytes(b"replacement")
        self.generator.execute.return_value = str(produced)

    def enter_patch(self, module, name):
        return self.enterContext(patch.object(module, name))

    def test_cli_default_cancel_and_confirmed_replacement(self):
        args = ["latextrans", "--config", str(self.config), "--project", str(self.paper)]
        for answer in ("", "y"):
            with patch("sys.argv", args), patch("builtins.input", return_value=answer) as prompt:
                self.assertEqual(main(), 0)
            self.assertIn(str(self.pdf), prompt.call_args.args[0])
            if not answer:
                self.parse.assert_not_called()
                self.assertEqual(self.pdf.read_bytes(), b"previous")
        self.parse.assert_called_once()
        self.assertEqual(self.pdf.read_bytes(), b"replacement")
        self.assertFalse((self.output / (self.name + "_3")).exists())

    def test_only_exact_output_path_triggers_confirmation(self):
        legacy_pdf = self.output / (self.name + "_2") / self.pdf.name
        self.pdf.rename(legacy_pdf)
        (self.pdf.parent / "figure.pdf").touch()
        with patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            completed, = run_translation(**self.request)["completed_projects"]
        self.assertEqual(completed["pdf_path"], str(self.pdf))
        self.assertEqual(legacy_pdf.read_bytes(), b"previous")
        self.pdf.write_bytes(b"")
        pending, = run_translation(**self.request)["pending_projects"]
        self.assertEqual(pending["pdf_path"], str(self.pdf))
        self.parse.assert_called_once()

    def test_stale_decision_and_failed_replacement_keep_latest_pdf(self):
        pending, = run_translation(**self.request)["pending_projects"]
        self.pdf.write_bytes(b"newer translation")
        updated, = run_translation(**pending["replace_request"])["pending_projects"]
        self.parse.assert_not_called()
        self.generator.execute.side_effect = OSError("compilation failed")
        failure, = run_translation(**updated["replace_request"])["failed_projects"]
        self.assertIn("compilation failed", failure["error"])
        self.assertEqual(self.pdf.read_bytes(), b"newer translation")

    def test_repeated_archive_extracts_only_after_confirmation(self):
        archive = self.sources.parent / "local.zip"
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("main.tex", "source")
        request = {**self.request, "project_items": [str(archive)]}
        first, = run_translation(**request)["completed_projects"]
        expected = self.output / "ch_local/ch_local.pdf"
        self.assertEqual(first["pdf_path"], str(expected))
        pending, = run_translation(**request)["pending_projects"]
        cancelled, = run_translation(**request, confirm_replace=lambda pdf: False)["cancelled_projects"]
        self.assertEqual(cancelled["input_archive"], str(archive))
        self.assertEqual(list(self.sources.glob("local*")), [self.sources / "local"])
        self.assertEqual(self.parse.call_count, 1)
        replacement = self.sources.parent / "new.pdf"
        replacement.write_bytes(b"updated")
        self.generator.execute.return_value = str(replacement)
        updated, = run_translation(**pending["replace_request"])["completed_projects"]
        self.assertEqual(updated["project_dir"], str(self.sources / "local_1"))
        self.assertEqual(updated["pdf_path"], str(expected))
        self.assertEqual(expected.read_bytes(), b"updated")
        self.assertEqual(list(self.output.glob("ch_local*")), [expected.parent])

    def test_legacy_client_and_current_client_share_the_first_publication_lock(self):
        name = "ch_fresh"
        legacy = self.output / ".latextrans" / (name + ".lock")
        with translation_slot(self.output, name):
            with self.assertRaisesRegex(RuntimeError, "already using"), _lock(legacy, name):
                pass
        with _lock(legacy, name):
            with self.assertRaisesRegex(RuntimeError, "already using"), translation_slot(self.output, name):
                pass

    def test_concurrent_attempt_cannot_publish_to_the_same_destination(self):
        with translation_slot(self.output, self.name):
            failure, = run_translation(**self.request)["failed_projects"]
        self.assertIn("already using", failure["error"])
        self.parse.assert_not_called()
        self.assertEqual(self.pdf.read_bytes(), b"previous")

    def test_gui_cancel_and_replace_from_confirmation(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/gui/streamlit_app.py"))
        def click(label):
            next(button for button in app.button if button.label == label).click().run()
            self.assertFalse(app.exception)

        with patch("src.configuration.DEFAULT_LOCAL_CONFIG_PATH", str(self.config)):
            app.run(timeout=30)
            next(field for field in app.text_area if field.label == "Local Projects or Archives").set_value(str(self.paper)).run()
            click("Start Translation")
            self.parse.assert_not_called()
            click("Cancel translation")
            self.assertEqual(self.pdf.read_bytes(), b"previous")
            click("Start Translation")
            click("Replace translation")
        self.parse.assert_called_once()
        self.assertEqual(self.pdf.read_bytes(), b"replacement")
        self.assertEqual(app.session_state.job_history[0]["pdfs"], [str(self.pdf)])
