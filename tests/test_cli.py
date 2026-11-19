import unittest
from unittest.mock import patch

from main import main


class TranslationCliTests(unittest.TestCase):
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
