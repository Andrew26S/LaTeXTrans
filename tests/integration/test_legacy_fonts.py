import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.formats.latex.reconstruct import LatexConstructor


@unittest.skipUnless(shutil.which("xelatex"), "requires XeLaTeX with CTeX and TeX Gyre")
class LegacyFontTests(unittest.TestCase):
    def compile_fonts(self, preamble, *, class_packages="", body=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "paper.cls").write_text(
            "\\ProvidesClass{paper}\n\\LoadClass{article}\n" + class_packages
        )
        source = (
            "\\documentclass{paper}\n" + preamble + r"""
\begin{document}
{\rmfamily\bfseries\typeout{TestRoman=\fontname\font}54.9}
{\sffamily\bfseries\typeout{TestSans=\fontname\font}Score}
{\ttfamily\bfseries\typeout{TestMono=\fontname\font}Model}
""" + body + "\n\\end{document}\n"
        )
        (root / "main.tex").write_text(source)
        LatexConstructor([{"trans_content": source}], [], [], [], [], str(root)).construct()
        run = subprocess.run(
            ["xelatex", "-halt-on-error", "-interaction=nonstopmode", "main.tex"],
            cwd=root, capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout[-1800:])
        return [line.split("=", 1)[1].replace(" ", "").lower()
                for line in run.stdout.splitlines()
                if line.startswith(("TestRoman=", "TestSans=", "TestMono="))]

    def test_legacy_packages_loaded_by_the_class(self):
        fonts = self.compile_fonts("", class_packages=r"\RequirePackage{times}")
        self.assertEqual(len(fonts), 3)
        for font, family in zip(fonts, ["termes", "heros", "cursor"]):
            self.assertIn(family, font)
            self.assertRegex(font, r"bold|/b\b")

    def test_explicit_fonts_before_and_after_grouped_packages(self):
        fonts = self.compile_fonts(r"""
\setmainfont{TeX Gyre Pagella}
\usepackage{times,graphicx}
\setmonofont{Latin Modern Mono Light}
""")
        for font, family in zip(fonts, ["pagella", "heros", "latinmodernmonolight"], strict=True):
            self.assertIn(family, font)

    def test_independent_text_families_and_no_legacy_package(self):
        default = self.compile_fonts("% \\usepackage{times}")
        mono = self.compile_fonts(r"\usepackage{courier}")
        self.assertEqual(mono[:2], default[:2])
        self.assertNotIn("cursor", default[2])
        self.assertIn("cursor", mono[2])
        times_math = self.compile_fonts(r"\usepackage{mathptmx}\usepackage[scaled=.91]{helvet}")
        self.assertIn("termes", times_math[0])
        self.assertIn("heros", times_math[1])
        self.assertEqual(times_math[2], default[2])

    def test_missing_tex_gyre_uses_latin_modern_bold_faces(self):
        fonts = self.compile_fonts(r"""
\usepackage{times}
\RenewDocumentCommand\IfFontExistsTF{m m m}{#3}
""")
        for font, family in zip(fonts, ["roman", "sans", "mono"], strict=True):
            self.assertIn(family, font)
            self.assertRegex(font, r"bold|/b\b")

    def test_direct_defaults_with_t1_and_microtype(self):
        fonts = self.compile_fonts(
            r"""
\renewcommand{\rmdefault}{ptm}
\renewcommand{\sfdefault}{phv}
\usepackage[T1]{fontenc}
\usepackage{microtype}
""",
            class_packages=r"\renewcommand{\ttdefault}{pcr}",
            body=r"\begin{itemize}\item 中文\footnote{Test}\end{itemize}",
        )
        for font, family in zip(fonts, ["termes", "heros", "cursor"], strict=True):
            self.assertIn(family, font)
            self.assertRegex(font, r"bold|/b\b")

    def test_direct_defaults_preserve_configured_opentype_faces(self):
        fonts = self.compile_fonts(r"""
\setmainfont{TeX Gyre Pagella}[BoldFont={TeX Gyre Bonum Bold}]
\renewcommand{\rmdefault}{ptm}
\renewcommand{\sfdefault}{phv}
\setsansfont{Latin Modern Sans}
\usepackage[T1]{fontenc}
\usepackage{microtype}
""", body=r"\begin{itemize}\item 中文\end{itemize}")
        self.assertIn("bonum", fonts[0])
        self.assertIn("latinmodernsans", fonts[1])
