import gzip
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.formats.latex.compile import LaTexCompiler
from src.formats.latex.utils import compile_with_latexmk


class CompilerIntegrationTests(unittest.TestCase):
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
