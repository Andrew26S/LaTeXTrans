import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import toml
from streamlit.testing.v1 import AppTest

from main import main
from src.agents.tool_agents.translator_agent import TranslatorAgent
from src.configuration import load_runtime_config
from src.runtime import run_translation


class SharedConfigurationTests(unittest.TestCase):
    def test_config_switch_and_configured_papers_match_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / "sources"
            paper = "2501.12948"
            project = sources / paper
            project.mkdir(parents=True)
            (project / "main.tex").touch()
            first, second = root / "one.toml", root / "two.toml"
            config = {
                "paper_list": [paper], "tex_sources_dir": str(sources), "output_dir": str(root / "out"),
                "mode": 2, "update_term": True, "user_term": "terms.csv",
                "llm_config": {"model": "configured-model"},
            }
            first.write_text(toml.dumps(config))
            second.write_text(toml.dumps({**config, "target_language": "ja"}))
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/gui/streamlit_app.py"))

            with (
                patch("src.configuration.DEFAULT_LOCAL_CONFIG_PATH", str(first)),
                patch("src.runtime.run_translation", wraps=run_translation) as run,
                patch("src.runtime.CoordinatorAgent") as agent,
            ):
                agent.return_value.workflow_latextrans.return_value = str(root / "paper.pdf")
                app.run(timeout=30)
                app.text_input(key=f"{first}:model").set_value("edited-model").run()
                app.selectbox(key=f"{first}:mode").set_value(0).run()
                app.checkbox(key=f"{first}:update_term").uncheck().run()
                self.assertFalse(app.exception)
                next(button for button in app.button if button.label == "Start Translation").click().run()
                self.assertEqual(run.call_args.kwargs["overrides"], {
                    "paper_list": [], "model": "edited-model", "mode": 0, "update_term": False,
                })

                next(field for field in app.text_input if field.label == "Config Path").set_value(str(second)).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.text_input(key=f"{second}:model").value, "configured-model")
                self.assertEqual(app.selectbox(key=f"{second}:mode").value, 2)
                self.assertTrue(app.checkbox(key=f"{second}:update_term").value)
                next(button for button in app.button if button.label == "Start Translation").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(run.call_args.kwargs["overrides"], {"paper_list": []})
                gui_args = agent.call_args.kwargs
                self.assertEqual(gui_args["project_dir"], str(project))
                self.assertEqual(app.session_state.job_history[0]["inputs"], [paper])

                with patch("sys.argv", ["latextrans", "--config", str(second)]):
                    self.assertEqual(main(), 0)
                self.assertEqual(agent.call_args.kwargs, gui_args)

    def test_current_and_history_retry_use_the_shared_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.toml"
            papers = ["2501.12948", "2501.12949"]
            path.write_text(toml.dumps({
                "paper_list": papers, "tex_sources_dir": str(root / "sources"),
                "output_dir": str(root / "out"), "category": {papers[0]: ["cs.AI"], papers[1]: []},
            }))
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/gui/streamlit_app.py"))
            with (
                patch("src.configuration.DEFAULT_LOCAL_CONFIG_PATH", str(path)),
                patch("src.runtime.prepare_arxiv_source", side_effect=lambda paper, folder: str(Path(folder, paper))),
                patch("src.runtime.run_translation", wraps=run_translation) as run,
                patch("src.runtime.CoordinatorAgent") as agent,
            ):
                agent.return_value.workflow_latextrans.side_effect = [ValueError("failed"), "ok.pdf", "ok.pdf", "ok.pdf"]
                app.run(timeout=30)
                app.text_input(key=f"{path}:model").set_value("selected").run()
                next(button for button in app.button if button.label == "Start Translation").click().run()
                failures = app.session_state.job_history[0]["failed_projects"]
                self.assertEqual([f["arxiv_id"] for f in failures], papers[:1])
                for label in ("Retry Failed Projects", "Retry Failed Projects from This Run"):
                    next(button for button in app.button if button.label == label).click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(run.call_args.kwargs["overrides"], {
                        "model": "selected", "replace_paper_list": True,
                        "paper_list": papers[:1], "category": {papers[0]: ["cs.AI"]},
                    })
                    self.assertEqual(app.session_state.job_history[0]["inputs"], papers[:1])
                    self.assertFalse(app.session_state.job_history[0]["failed_projects"])

    def test_term_updates_apply_in_terminology_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.toml")
            for setting, expected in [('true', True), ('"True"', True), ('false', False), ('"False"', False)]:
                with self.subTest(setting=setting):
                    path.write_text(f"update_term = {setting}\n")
                    agent = TranslatorAgent(config=load_runtime_config(str(path)), trans_mode=2)
                    agent._request_llm_for_trans = AsyncMock(return_value="译文")
                    agent._request_llm_for_extract_terms = AsyncMock(return_value='"term" - "术语"')
                    asyncio.run(agent._translate_section({"section": "1", "content": "term"}, None))
                    self.assertIs(agent.update_term, expected)
                    self.assertEqual(agent._request_llm_for_extract_terms.await_count, int(expected))
                    self.assertEqual(agent.term_dict, {"term": "术语"} if expected else {})

    def test_explicitly_clear_optional_config_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.toml")
            path.write_text('user_term = "terms.csv"\n[llm_config]\napi_key = "configured"\n')
            config = load_runtime_config(str(path), {"user_term": "", "key": ""})
            self.assertEqual(config["user_term"], "")
            self.assertEqual(config["llm_config"]["api_key"], "")
