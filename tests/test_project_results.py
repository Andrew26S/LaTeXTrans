import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch

from src.agents import coordinator_agent
from src.formats.latex.compile import LaTexCompiler
from src.gui.streamlit_app import _collect_result_pdfs
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
                result = run_projects({}, projects, str(output), events.append)

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
            self.assertEqual(_collect_result_pdfs(result), [str(final_pdf)])

    def test_compiler_ignores_pdfs_from_previous_builds_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(r"\documentclass{article}")
            previous = root / "build_pdflatex"
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

    def test_failed_pdf_is_rejected_and_fallback_uses_reported_jobname(self):
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
                self.assertEqual(compiler.compile(), str(root / "build_xelatex" / "custom job.pdf"))
