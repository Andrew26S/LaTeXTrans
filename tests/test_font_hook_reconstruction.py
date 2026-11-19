import unittest

from src.formats.latex.legacy_fonts import add_legacy_font_hooks


class LegacyFontHookTests(unittest.TestCase):
    def test_reconstruction_replaces_old_hooks_even_after_comments_are_stripped(self):
        directive = "% !TeX program = xelatex\n"
        document = r"\documentclass{article}"
        hooks = r"""\ifdefined\XeTeXversion
\ExplSyntaxOn
\cs_new_protected:Npn \latextrans_legacy_font:nnNNnn #1#2#3#4#5#6 {}
\ExplSyntaxOff
\fi
"""
        expected = add_legacy_font_hooks(directive + document)
        for header in ("", "% LaTeXTrans legacy text fonts\n"):
            self.assertEqual(add_legacy_font_hooks(directive + header + hooks + document), expected)
        self.assertEqual(add_legacy_font_hooks(expected), expected)
