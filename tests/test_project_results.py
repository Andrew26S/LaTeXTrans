import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch

from src.agents import coordinator_agent
from src.formats.latex.compile import LaTexCompiler
from src.formats.latex.utils import compile_with_latexmk
from src.runtime import run_projects


class ProjectResultTests(unittest.TestCase):
    def test_batch_reports_generation_failures_and_only_offers_current_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = [str(root / name) for name in ("old", "broken", "ok")]
            output = root / "outputs"
            old_pdf = output / "ch_old" / "ch_old.pdf"
            old_pdf.parent.mkdir(parents=True)
            old_pdf.write_bytes(b"previous run")
            generated = root / "new.pdf"
            generated.write_bytes(b"current run")
            events = []

            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch.object(coordinator_agent, "GeneratorAgent") as generator,
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                generator.return_value.execute.side_effect = [None, OSError("disk full"), str(generated)]
                result = run_projects({}, projects, str(output), events.append, confirm_replace=lambda pdf: True)

            failures = result["failed_projects"]
            self.assertEqual([p["project_dir"] for p in failures], projects[:2])
            self.assertIn("no translated PDF", failures[0]["error"])
            self.assertIn("disk full", failures[1]["error"])
            final_pdf = output / "ch_ok" / "ch_ok.pdf"
            self.assertEqual([p["pdf_path"] for p in result["completed_projects"]], [str(final_pdf)])
            self.assertEqual(final_pdf.read_bytes(), b"current run")
            self.assertEqual(old_pdf.read_bytes(), b"previous run")
            self.assertEqual([e["type"] for e in events], [
                "project_start", "project_error", "project_start", "project_error",
                "project_start", "project_complete",
            ])
            self.assertEqual(events[-1]["pdf_path"], str(final_pdf))
            self.assertEqual(result["pdfs"], [str(final_pdf)])

    def test_compiler_ignores_pdfs_from_previous_builds_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(r"\documentclass{article}")
            previous = root / "build_xelatex"
            previous.mkdir()
            (previous / "main.pdf").write_bytes(b"old build")
            compiler = LaTexCompiler(directory)

            def build_figure(cmd, **kwargs):
                out_dir = Path(next(arg.removeprefix("-outdir=") for arg in cmd if arg.startswith("-outdir=")))
                report = Path(next(arg.removeprefix("-deps-out=") for arg in cmd if arg.startswith("-deps-out=")))
                (out_dir / "figure.pdf").write_bytes(b"not the paper")
                report.write_text(f"{out_dir / 'main.pdf'} : main.tex")
                return CompletedProcess(cmd, 0)

            with patch("src.formats.latex.compile.subprocess.run", side_effect=build_figure):
                self.assertIsNone(compiler.compile())
            self.assertEqual([(a["engine"], a["status"]) for a in compiler.attempts], [("xelatex", "failed")])
            self.assertTrue(all(a["pdf_path"] is None for a in compiler.attempts))

    def test_source_compile_rejects_failed_pdf_and_fallback_uses_reported_jobname(self):
        with tempfile.TemporaryDirectory(prefix="paper with spaces ") as directory:
            root = Path(directory)
            (root / "main.tex").write_text(r"\documentclass{article}")
            compiler = LaTexCompiler(directory)

            def run_engine(cmd, **kwargs):
                build = root / ("build_pdflatex" if "-pdflatex" in cmd else "build_xelatex")
                pdf = build / "custom job.pdf"
                pdf.write_bytes(b"partial or complete")
                report = Path(next(arg.removeprefix("-deps-out=") for arg in cmd if arg.startswith("-deps-out=")))
                report.write_text(f"#===Dependents, and related info, for main.tex:\n{pdf.relative_to(root)} : main.tex")
                return CompletedProcess(cmd, 12 if "-pdflatex" in cmd else 0)

            with patch("src.formats.latex.compile.subprocess.run", side_effect=run_engine):
                self.assertEqual(compiler.compile_source(root), str(root / "custom job.pdf"))
            self.assertEqual([(a["status"], a["exit_code"]) for a in compiler.attempts], [
                ("pdf_with_errors", 12), ("success", 0),
            ])
            for attempt in compiler.attempts:
                self.assertTrue(Path(attempt["log_path"]).is_file())
                self.assertTrue(Path(attempt["pdf_path"]).is_file())

    def test_partial_pdf_is_reported_on_failure_even_if_fallback_produces_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "paper"
            partial = root / "partial.pdf"
            partial.write_bytes(b"partial")
            attempts = [
                dict(engine="pdflatex", exit_code=12, status="pdf_with_errors",
                     pdf_path=str(partial), log_path=str(root / "first.log")),
                dict(engine="xelatex", exit_code=12, status="failed",
                     pdf_path=None, log_path=str(root / "second.log")),
            ]
            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch.object(coordinator_agent, "GeneratorAgent") as generator,
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                generator.return_value.execute.return_value = None
                generator.return_value.compile_attempts = attempts
                events = []
                result = run_projects({}, [str(project)], str(root), events.append)

            self.assertFalse(result["completed_projects"])
            failure, = result["preview_projects"]
            self.assertEqual(failure["compilation_status"], "pdf_with_errors")
            self.assertEqual(failure["compile_attempts"], attempts)
            self.assertEqual(events[-1], {"type": "project_preview", **failure})
            self.assertFalse((root / "ch_paper" / "ch_paper.pdf").exists())

    def test_missing_latexmk_records_diagnostic_and_clears_previous_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(r"\documentclass{article}")
            marker = root / "success.txt"
            marker.write_text("Compilation successful\n")
            compiler = LaTexCompiler(directory)
            with patch("src.formats.latex.compile.subprocess.run", side_effect=FileNotFoundError("missing latexmk")):
                self.assertIsNone(compiler.compile_ja())
            attempt, = compiler.attempts
            self.assertEqual((attempt["engine"], attempt["status"], attempt["exit_code"]), ("lualatex", "failed", None))
            self.assertIn("missing latexmk", Path(attempt["log_path"]).read_text())
            self.assertFalse(marker.exists())

    def test_compiler_exception_before_first_attempt_keeps_reconstructed_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "paper"
            project.mkdir()
            main = r"\documentclass{article}\begin{document}Original.\end{document}"
            (project / "main.tex").write_text(main)
            maps = {"sections_map.json": [{"trans_content": main.replace("Original.", "译文")}]}
            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch.object(coordinator_agent.GeneratorAgent, "read_file", side_effect=lambda p, fmt: maps.get(p.name, [])),
                patch.object(LaTexCompiler, "compile", side_effect=OSError("build unavailable")),
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                failed, = run_projects({}, [str(project)], str(root / "out"))["failed_projects"]
            self.assertEqual(failed["error"], "build unavailable")
            self.assertIn("译文", (Path(failed["recovery_run"]) / "project/main.tex").read_text())

    def test_standalone_and_agent_publish_reported_pdf_and_map_from_their_working_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            main = src / "main.tex"
            main.write_text(r"\documentclass{article}")
            output = root / "out"

            def build(cmd, cwd, **kwargs):
                self.assertEqual(cmd[:2], ["latexmk", "-pdflatex"])
                self.assertIn("-synctex=1", cmd)
                self.assertEqual(Path(cmd[-1]), main)
                build_dir = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("-outdir=")))
                pdf = build_dir / "custom job.pdf"
                pdf.write_bytes(b"paper")
                pdf.with_suffix(".synctex.gz").write_bytes(b"source map")
                report = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("-deps-out=")))
                report.write_text(f"{os.path.relpath(pdf, cwd)} : main.tex")
                return CompletedProcess(cmd, 0)

            with patch("src.formats.latex.compile.subprocess.run", side_effect=build) as latexmk:
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    compile_with_latexmk("src/main.tex", "out")
                finally:
                    os.chdir(previous)
                self.assertEqual(latexmk.call_args.kwargs["cwd"], root)
                self.assertEqual((output / "custom job.pdf").read_bytes(), b"paper")
                self.assertEqual((output / "custom job.synctex.gz").read_bytes(), b"source map")
                agent_output = root / "agent"
                pdf = LaTexCompiler(str(src)).compile_source(agent_output)
                self.assertEqual(latexmk.call_args.kwargs["cwd"], src)
                self.assertEqual(pdf, str(agent_output / "custom job.pdf"))
                self.assertEqual(Path(pdf).with_suffix(".synctex.gz").read_bytes(), b"source map")

    def test_publishing_without_synctex_removes_previous_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(r"\documentclass{article}")
            (root / "main.synctex.gz").write_bytes(b"old map")

            def build(cmd, **kwargs):
                target = root / "build_pdflatex/main.pdf"
                target.write_bytes(b"new pdf")
                report = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("-deps-out=")))
                report.write_text(f"{target.relative_to(root)} : main.tex")
                return CompletedProcess(cmd, 0)

            with patch("src.formats.latex.compile.subprocess.run", side_effect=build):
                self.assertEqual(LaTexCompiler(directory).compile_source(root), str(root / "main.pdf"))
            self.assertFalse((root / "main.synctex.gz").exists())

    def test_same_named_projects_require_replacement_and_keep_failed_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = []
            for content in ("broken", "good"):
                project = root / content / "paper"
                project.mkdir(parents=True)
                (project / "main.tex").write_text(r"\documentclass{article}" + content)
                (project / "paper.pdf").write_bytes(content.encode())
                projects.append(str(project))

            def build(cmd, stdout, **kwargs):
                content = Path(cmd[-1]).read_bytes()
                target_dir = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("-outdir=")))
                target = target_dir / "main.pdf"
                target.write_bytes(content)
                stdout.write(content)
                report = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("-deps-out=")))
                report.write_text(f"{target} : main.tex")
                return CompletedProcess(cmd, 12 if b"broken" in content else 0)

            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch("src.formats.latex.delivery.pdf_page_count", return_value=1),
                patch.object(coordinator_agent.GeneratorAgent, "read_file", return_value=[]),
                patch("src.formats.latex.reconstruct.LatexConstructor"),
                patch("src.formats.latex.compile.subprocess.run", side_effect=build),
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                first = run_projects({}, projects, str(root / "outputs"))
                translated, original = first["pdfs"]
                self.assertTrue(Path(translated).read_bytes().endswith(b"good"))
                self.assertEqual(Path(original).read_bytes(), b"good")
                Path(projects[0], "main.tex").write_text(r"\documentclass{article}fixed")
                pending, = run_projects({}, projects[:1], str(root / "outputs"))["pending_projects"]
                self.assertEqual(pending["pdf_path"], translated)
                retry = run_projects({}, projects[:1], str(root / "outputs"), confirm_replace=lambda pdf: True)

            attempts = first["preview_projects"][0]["compile_attempts"]
            self.assertEqual([attempt["engine"] for attempt in attempts], ["xelatex"])
            history = root / "outputs" / ".latextrans" / "ch_paper"
            failed_build = history / "run-001" / "build_xelatex"
            for attempt in attempts:
                self.assertEqual(Path(attempt["pdf_path"]), failed_build / "main.pdf")
                self.assertEqual(Path(attempt["log_path"]), failed_build / "latexmk-output.txt")
                self.assertTrue(Path(attempt["pdf_path"]).read_bytes().endswith(b"broken"))
                self.assertTrue(Path(attempt["log_path"]).read_bytes().endswith(b"broken"))
            for number, expected in [(2, b"good"), (3, b"fixed")]:
                log = history / f"run-{number:03d}" / "build_xelatex" / "latexmk-output.txt"
                self.assertTrue(log.read_bytes().endswith(expected))
            self.assertFalse(list(history.glob("run-*/work")))
            self.assertEqual(retry["pdfs"], first["pdfs"])
            self.assertTrue(Path(translated).read_bytes().endswith(b"fixed"))
            self.assertEqual(Path(original).read_bytes(), b"broken")

    def test_gui_marks_all_result_downloads_to_stay_on_current_run(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            success = root / "ok.pdf"
            success.write_bytes(b"ok")
            attempts = []
            for engine in ("pdflatex", "xelatex"):
                partial = root / f"{engine}.pdf"
                partial.write_bytes(b"partial")
                attempts.append(dict(engine=engine, status="pdf_with_errors", pdf_path=str(partial)))
            app = AppTest.from_string("""
import streamlit as st
from src.gui.streamlit_app import _ensure_session_state, _render_result_files
_ensure_session_state()
_render_result_files(st.session_state.result, {"project_items": [], "overrides": {}})
""")
            app.session_state.current_run_logs = []
            app.session_state.result = {
                "projects": ["ok", "partial"], "output_dir": directory, "pdfs": [str(success)], "config": {}, "retry_request": {},
                "completed_projects": [{"project_name": "ok", "pdf_path": str(success)}],
                "failed_projects": [{"index": 2, "project_name": "partial", "project_dir": "partial",
                                     "error": "failed", "compile_attempts": attempts}],
            }
            app.run(timeout=30)
            self.assertFalse(app.exception)
            buttons = app.get("download_button")
            self.assertEqual(len(buttons), 3)
            self.assertTrue(all(button.proto.ignore_rerun for button in buttons))

    def test_gui_recompiles_saved_project_without_replacing_failed_status(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            pdf = run / "fixed.pdf"
            pdf.write_bytes(b"recompiled")
            attempt = dict(engine="xelatex", status="success", pdf_path=str(pdf), log_path=str(run / "log"))
            app = AppTest.from_string("""
import streamlit as st
from src.gui.streamlit_app import _render_history
_render_history()
""")
            app.session_state.job_history = [{
                "timestamp": "test", "inputs": [], "output_dir": directory, "pdfs": [], "logs": [],
                "completed_projects": [], "failed_projects": [{
                    "project_name": "paper", "compilation_status": "pdf_with_errors", "recovery_run": directory,
                }],
            }]
            app.run()
            with patch("src.gui.streamlit_app.recompile_translation", return_value=attempt) as recompile:
                next(button for button in app.button if button.label == "Recompile saved project").click().run()
                recompile.assert_called_once_with(run)
            self.assertFalse(app.exception)
            saved = app.session_state.job_history[0]["failed_projects"][0]
            self.assertEqual(saved["compilation_status"], "pdf_with_errors")
            self.assertEqual(saved["recompile_attempt"], attempt)
            self.assertEqual(len(app.get("download_button")), 1)
