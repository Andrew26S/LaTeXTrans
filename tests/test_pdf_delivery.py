import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.formats.latex.delivery import assess_pdf


MICROTYPE = r"""./template.cls:25: Package microtype Error: Disabling ligatures of a font is on
ly possible
(microtype)                with pdftex version 1.30 or newer.
(microtype)                Ignoring \DisableLigatures.

"""
BABEL = """! Package babel Error: Unknown option 'latin'. Either you misspelled it
(babel) or the language definition file latin.ldf was not found.
(babel) There is a locale ini file for this language.

(/tex/generic/babel-english/english.ldf)
"""
FINISH = """Output written on /tmp/paper.xdv (3 pages, 200 bytes).
Collected error summary (may duplicate other messages):
  xelatex: Command for 'xelatex' gave return code 1
      Refer to 'paper.log' and/or above output for details
"""


class PdfDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pdf = Path(tmp.name, "paper.pdf")
        self.pdf.touch()
        self.pdf.write_bytes(b"test PDF")
        self.log = Path(tmp.name, "latexmk-output.txt")

    def assess(self, text, pages=3, exit_code=12):
        self.log.write_text(text)
        with patch("src.formats.latex.delivery.pdf_page_count", return_value=pages):
            return assess_pdf(self.pdf, self.log, "xelatex", exit_code)

    def test_template_notices_and_final_unresolved_references(self):
        log = (MICROTYPE + "Citation `from-first-pass' on page 1 undefined\nThis is XeTeX, version 1\n"
               + MICROTYPE + BABEL + "LaTeX Warning: Reference `fig:a' on page 1 undefined.\n" + FINISH)
        status, notes = self.assess(log)
        self.assertEqual(status, "ready_with_warnings")
        self.assertEqual(len(notes), 3)
        self.assertIn("Unresolved reference: fig:a", notes)

    def test_successful_exit_uses_only_final_pass_reference_warnings(self):
        first = "LaTeX Warning: Reference `previous' on page 1 undefined.\nThis is XeTeX, Version 1\n"
        final = "LaTeX Warning: Reference `missing' on page 2 undefined.\n"
        citation = "Package natbib Warning: Citation `key' on page 2 undefined.\n"
        self.assertEqual(self.assess(first, exit_code=0), ("ready", []))
        self.assertEqual(self.assess(first + final * 2 + citation, exit_code=0), (
            "ready_with_warnings", ["Unresolved reference: missing", "Unresolved citation: key"],
        ))

    def test_interruption_missing_input_and_unrecognized_errors_override_known_notices(self):
        for extra in [
            "! Emergency stop.\n", "./sections/a.tex:18: Undefined control sequence.\n",
            "No file chapters/results.tex.\n", "! LaTeX Error: File 'figure.png' not found.\n",
            "Missing character: There is no 字 in font cmr!\n", "! Package babel Error: Unknown option 'other'.\n",
        ]:
            with self.subTest(extra=extra):
                self.assertEqual(self.assess(MICROTYPE + extra + FINISH)[0], "preview")
        self.assertEqual(self.assess(MICROTYPE + FINISH + "  bibtex paper: command failed\n")[0], "preview")
        self.assertEqual(self.assess(BABEL.replace("babel-english/english.ldf", "babel-latin/latin.ldf") + FINISH)[0], "preview")
        self.assertEqual(self.assess(MICROTYPE + FINISH.replace("paper.xdv", "figure.xdv"))[0], "preview")
        self.assertEqual(self.assess(FINISH)[0], "preview")

    def test_page_count_and_basic_readability(self):
        for pages, result in [(0, "failed"), (None, "preview"), (2, "preview")]:
            with self.subTest(pages=pages):
                self.assertEqual(self.assess(MICROTYPE + FINISH, pages=pages)[0], result)
        self.assertEqual(self.assess("No file sections/results.tex.\n", exit_code=0)[0], "preview")
        self.assertEqual(self.assess("", exit_code=0)[0], "ready")
        self.assertEqual(self.assess("", pages=0, exit_code=0)[0], "failed")
        self.pdf.unlink()
        self.assertEqual(self.assess(FINISH)[0], "failed")
