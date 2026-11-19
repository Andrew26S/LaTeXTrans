import unittest

from src.agents.tool_agents.validator_agent import ValidatorAgent
from src.formats.latex.parser import LatexParser
from src.formats.latex.utils import find_extra_document_structure, sanitize_llm_translation


class SanitizeTranslationTests(unittest.TestCase):
    def test_removes_end_document_not_in_source(self):
        src = "\\begin{document}\n\\maketitle\n\\section{Intro}\nHello."
        trans = "\\begin{document}\n\\maketitle\n\\section{引言}\n你好。\n\n\\end{document}"
        self.assertEqual(
            sanitize_llm_translation(src, trans),
            "\\begin{document}\n\\maketitle\n\\section{引言}\n你好。",
        )

    def test_removes_added_documentclass_and_begin_document(self):
        src = "\\section{Intro}\nHello."
        trans = "\\documentclass[11pt]{article}\n\\begin{document}\n\\section{引言}\n你好。\n\\end{document}"
        self.assertEqual(sanitize_llm_translation(src, trans), "\\section{引言}\n你好。")

    def test_keeps_structure_present_in_source(self):
        src = "Text.\n\\end{document}"
        trans = "文本。\n\\end{document}"
        self.assertEqual(sanitize_llm_translation(src, trans), trans)

    def test_strips_code_fence(self):
        src = "\\section{Intro} Hello."
        self.assertEqual(sanitize_llm_translation(src, "```latex\n\\section{引言} 你好。\n```"), "\\section{引言} 你好。")
        self.assertEqual(sanitize_llm_translation(src, "```\n\\section{引言} 你好。\n```"), "\\section{引言} 你好。")

    def test_keeps_code_fence_when_source_has_one(self):
        src = "```python\nx = 1\n```"
        self.assertEqual(sanitize_llm_translation(src, src), src)

    def test_find_extra_document_structure(self):
        self.assertEqual(
            find_extra_document_structure("a", "b \\end{document}"),
            {"\\end{document}": (0, 1)},
        )
        self.assertEqual(find_extra_document_structure("a \\end{document}", "b \\end{document}"), {})


class MergeShortSectionsTests(unittest.TestCase):
    def test_untranslated_sections_are_never_merged(self):
        parser = LatexParser(".", ".")
        parser.sections_json = [
            {"section": "-1", "content": "\\documentclass{article}", "trans_content": "\\documentclass{article}"},
            {"section": "0", "content": "\\begin{document}\n\\maketitle", "trans_content": "\\begin{document}\n\\maketitle"},
            {"section": "1", "content": "\\section{Intro}\n" + "word " * 100},
            {"section": "2", "content": "\\section{Short}"},
            {"section": "3", "content": "\\section{Long}\n" + "word " * 100},
        ]
        parser._merge_short_sections(min_tokens=50)
        self.assertEqual([s["section"] for s in parser.sections_json], ["-1", "0", "1", "2+3"])

    def test_short_tail_merges_into_translatable_section_only(self):
        parser = LatexParser(".", ".")
        parser.sections_json = [
            {"section": "-1", "content": "\\documentclass{article}", "trans_content": ""},
            {"section": "0", "content": "\\begin{document}", "trans_content": ""},
            {"section": "1", "content": "\\section{Only}"},
        ]
        parser._merge_short_sections(min_tokens=50)
        self.assertEqual([s["section"] for s in parser.sections_json], ["-1", "0", "1"])


class ValidatorStructureTests(unittest.TestCase):
    def test_reports_added_end_document(self):
        validator = ValidatorAgent(config={})
        part = {
            "section": "1",
            "content": "\\section{Intro}\nHello \\cite{a}.",
            "trans_content": "\\section{引言}\n你好 \\cite{a}。\n\\end{document}",
        }
        report = validator._validate(part)
        self.assertIsNotNone(report)
        self.assertIn("\\end{document}", report["command_error"])

    def test_clean_translation_passes(self):
        validator = ValidatorAgent(config={})
        part = {
            "section": "1",
            "content": "\\section{Intro}\nHello \\cite{a}.",
            "trans_content": "\\section{引言}\n你好 \\cite{a}。",
        }
        self.assertIsNone(validator._validate(part))


if __name__ == "__main__":
    unittest.main()
