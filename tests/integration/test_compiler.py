import gzip
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.agents import coordinator_agent
from src.formats.latex.compile import LaTexCompiler
from src.formats.latex.utils import compile_with_latexmk
from src.runtime import recompile_translation, run_projects
from tests.integration.tex_prerequisites import require_xelatex_fixture


class CompilerIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("xelatex"), "requires XeLaTeX and latexmk")
    def test_recompile_without_retranslation_after_missing_compiler(self):
        require_xelatex_fixture(self)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "paper"
            src.mkdir()
            source = r"\documentclass{article}\begin{document}Original.\end{document}"
            (src / "main.tex").write_text(source)
            maps = {"sections_map.json": [{"trans_content": source.replace("Original.", r"译文 \ref{missing}.")}]}
            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch.object(coordinator_agent.GeneratorAgent, "read_file", side_effect=lambda p, fmt: maps.get(p.name, [])),
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                with patch("src.formats.latex.compile.subprocess.run", side_effect=FileNotFoundError("missing latexmk")):
                    result = run_projects({}, [str(src)], str(root / "out"))
                failure, = result["failed_projects"]
                self.assertEqual(failure["compilation_status"], "failed")
                run = Path(failure["recovery_run"])
                self.assertIn("译文", (run / "project/main.tex").read_text())
                self.assertFalse((run / "work").exists())
                shutil.rmtree(src)
                attempt = recompile_translation(run)
                self.assertEqual((attempt["exit_code"], attempt["status"]), (0, "success"))
                self.assertEqual(attempt["delivery_status"], "ready_with_warnings")
                self.assertIn("Unresolved reference: missing", attempt["diagnostics"])
                self.assertTrue(Path(attempt["pdf_path"]).is_file())
                translator.return_value.execute.assert_awaited_once()

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("xelatex"), "requires XeLaTeX with CTeX")
    def test_recompile_edited_project_after_partial_pdf(self):
        require_xelatex_fixture(self, packages=("microtype",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "paper"
            (src / "chapters").mkdir(parents=True)
            main = r"\documentclass{article}\usepackage{local}\begin{document}\input{chapters/body}\end{document}"
            (src / "main.tex").write_text(main)
            (src / "chapters/body.tex").write_text("Original text")
            (src / "local.sty").write_text(r"\PackageError{local}{Template requires repair}{}")
            begin, end = "<PLACEHOLDER_chapters/body_begin>", "<PLACEHOLDER_chapters/body_end>"
            maps = {
                "sections_map.json": [{"trans_content": main.replace(r"\input{chapters/body}", begin + "译文" + end)}],
                "inputs_map.json": [{"begin": begin, "end": end, "path": "chapters/body", "command": r"\input{chapters/body}"}],
            }
            with (
                patch.object(coordinator_agent, "ParserAgent"),
                patch.object(coordinator_agent, "TranslatorAgent") as translator,
                patch.object(coordinator_agent, "ValidatorAgent") as validator,
                patch.object(coordinator_agent.GeneratorAgent, "read_file", side_effect=lambda p, fmt: maps.get(p.name, [])),
            ):
                translator.return_value.execute = AsyncMock()
                validator.return_value.execute.return_value = []
                result = run_projects({}, [str(src)], str(root / "out"))
                self.assertEqual(result["failed_projects"], [])
                failed, = result["preview_projects"]
                self.assertEqual(failed["compilation_status"], "pdf_with_errors", failed)
                run = Path(failed["recovery_run"])
                self.assertEqual(run, root / "out/.latextrans/ch_paper/run-001")
                project = run / "project"
                self.assertEqual((project / "chapters/body.tex").read_text().strip(), "译文")
                self.assertFalse((run / "work").exists())
                original = failed["compile_attempts"][0]
                original_pdf = Path(original["pdf_path"]).read_bytes()
                original_log = Path(original["log_path"]).read_bytes()
                shutil.rmtree(src)  # The retained project must stand on its own.
                retry = recompile_translation(run)
                self.assertEqual(retry["status"], "pdf_with_errors")
                (project / "local.sty").write_text(r"\ProvidesPackage{local}")
                fixed = recompile_translation(run)
                self.assertEqual(fixed["status"], "success", Path(fixed["log_path"]).read_text(errors="replace")[-2500:])
                self.assertTrue(Path(fixed["pdf_path"]).is_relative_to(run / "recompile-002"))
                self.assertTrue(Path(retry["pdf_path"]).is_file())
                self.assertEqual(Path(original["pdf_path"]).read_bytes(), original_pdf)
                self.assertEqual(Path(original["log_path"]).read_bytes(), original_log)
                translator.return_value.execute.assert_awaited_once()

                src.mkdir()
                (src / "main.tex").write_text(main)
                (src / "chapters").mkdir()
                (src / "chapters/body.tex").write_text("Original text")
                (src / "local.sty").write_text(r"\RequirePackage{microtype}\DisableLigatures[f]{family=*}")
                result = run_projects({}, [str(src)], str(root / "out"))
                published, = result["completed_projects"]
                self.assertEqual(result["preview_projects"], [])
                self.assertEqual(published["delivery_status"], "ready_with_warnings")
                self.assertEqual(published["compilation_status"], "pdf_with_errors")
                public_pdf = Path(published["pdf_path"])
                self.assertTrue(public_pdf.is_file())
                self.assertTrue((public_pdf.parent / "paper/main.tex").is_file())
                self.assertTrue((public_pdf.parent / "compilation_report.json").is_file())
                self.assertTrue((Path(published["recovery_run"]) / "project/main.tex").is_file())
                self.assertEqual(recompile_translation(published["recovery_run"])["delivery_status"], "ready_with_warnings")

                (src / "local.sty").write_text(r"\PackageError{local}{New unknown error}{}")
                before = public_pdf.read_bytes()
                result = run_projects({}, [str(src)], str(root / "out"), confirm_replace=lambda pdf: True)
                preview, = result["preview_projects"]
                self.assertEqual(preview["delivery_status"], "preview")
                self.assertEqual(public_pdf.read_bytes(), before)

    @unittest.skipUnless(shutil.which("latexmk") and shutil.which("pdflatex"), "requires TeX Live")
    def test_standalone_root_relative_includes_and_agent_source_maps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_home = root / "config"
            blank_rc = config_home / "latexmk" / "latexmkrc"
            blank_rc.parent.mkdir(parents=True)
            blank_rc.write_text("# Isolated smoke test configuration\n")
            environment = patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(config_home), "LATEXMKRCSYS": str(blank_rc),
            })
            environment.start()
            self.addCleanup(environment.stop)
            src = root / "src"
            src.mkdir()
            (src / "body.tex").write_text("Hello")
            main = src / "main.tex"
            document = r"\documentclass{article}\begin{document}\input{%s}\end{document}"
            main.write_text(document % "src/body")
            (root / ".latexmkrc").write_text("$jobname = 'article';\n")
            cwd = Path.cwd()
            try:
                os.chdir(root)
                compile_with_latexmk("src/main.tex", "out")
            finally:
                os.chdir(cwd)
            self.assertTrue((root / "out/article.pdf").is_file())
            self.assertIn(b"src/body.tex", gzip.decompress((root / "out/article.synctex.gz").read_bytes()))

            # The agent resolves includes from the main file's directory.
            main.write_text(document % "body")
            compiler = LaTexCompiler(str(src))
            pdf = compiler.compile_source(root / "out")
            self.assertEqual(pdf, str(root / "out/main.pdf"))
            self.assertIn(b"body.tex", gzip.decompress(Path(pdf).with_suffix(".synctex.gz").read_bytes()))
