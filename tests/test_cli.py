import unittest
from unittest.mock import patch

from main import confirm_replace, main


class TranslationCliTests(unittest.TestCase):
    @patch("main.run_translation")
    @patch("main.recompile_translation")
    def test_compile_only_uses_saved_run_and_reports_compiler_status(self, compile_only, translate):
        for args, path, status, code in [
            ([], ".", "success", 0),
            (["run with spaces"], "run with spaces", "pdf_with_errors", 1),
            (["empty-run"], "empty-run", "failed", 1),
        ]:
            with self.subTest(status=status), patch("sys.argv", ["latextrans", "--compile-only", *args]):
                compile_only.return_value = {"status": status}
                self.assertEqual(main(), code)
                compile_only.assert_called_with(path)
        translate.assert_not_called()

    @patch("main.recompile_translation")
    def test_compile_only_uses_delivery_verdict_for_exit_code(self, compile_only):
        for status, delivery, code in [
            ("pdf_with_errors", "ready_with_warnings", 0),
            ("pdf_with_errors", "preview", 1),
            ("success", "failed", 1),
        ]:
            compile_only.return_value = {"status": status, "delivery_status": delivery}
            with self.subTest(delivery=delivery), patch("sys.argv", ["latextrans", "--compile-only"]):
                self.assertEqual(main(), code)

    @patch("main.run_translation")
    def test_passes_overrides_and_comma_separated_inputs(self, translate):
        translate.return_value = {"completed_projects": [{}], "failed_projects": [{}]}
        argv = [
            "latextrans", "--config", "custom.toml", "--model", "model-name",
            "--url", "service-url", "--key", "test-key", "--source", "sources",
            "--output", "results", "--arxiv", "2508.18791v2,", "2407.01648",
            "--project", "paper one.zip,", "paper two",
        ]
        with patch("sys.argv", argv):
            self.assertEqual(main(), 1)
        translate.assert_called_once_with(
            config_path="custom.toml",
            overrides={
                "model": "model-name", "url": "service-url", "key": "test-key",
                "source": "sources", "output": "results",
                "paper_list": ["2508.18791v2", "2407.01648"],
            },
            project_items=["paper one.zip", "paper two"],
            all_existing=False,
            confirm_replace=confirm_replace,
        )

    @patch("main.run_translation")
    def test_all_existing_uses_local_config(self, translate):
        translate.return_value = {"completed_projects": [{}], "failed_projects": []}
        with patch("sys.argv", ["latextrans", "--all-existing"]):
            self.assertEqual(main(), 0)
        args = translate.call_args.kwargs
        self.assertEqual(args["config_path"], "config/local.toml")
        self.assertTrue(args["all_existing"])
        self.assertEqual(args["project_items"], [])
        self.assertEqual(args["overrides"]["paper_list"], [])
