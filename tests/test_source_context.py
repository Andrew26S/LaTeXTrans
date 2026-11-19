import unittest

from src.formats.latex.source_context import build_source_contexts


class SourceContextTests(unittest.TestCase):
    def test_merged_sections_and_nested_captions_use_source_paths(self):
        caption_id = "<PLACEHOLDER_CAP_1>"
        title_id = "<PLACEHOLDER_CAP_2>"
        abstract_id = "<PLACEHOLDER_ENV_1>"
        figure_id = "<PLACEHOLDER_ENV_2>"
        sections = [
            {"section": "-1", "content": title_id},
            {"section": "0", "content": abstract_id},
            {"section": "1+1_1", "content": r"\section{Results}\subsection*{Ablation}" + figure_id},
            {"section": "1_2", "content": r"\subsection{Latency}"},
            {"section": "2", "content": r"\section{Discussion}"},
        ]
        captions = [
            {"placeholder": caption_id, "content": r"\caption{Error}"},
            {"placeholder": title_id, "content": r"\icmltitle{Paper {A}}", "trans_content": "translated"},
        ]
        envs = [
            {"placeholder": abstract_id, "content": r"\begin{abstract}Original abstract.\end{abstract}"},
            {"placeholder": figure_id, "content": caption_id},
        ]
        contexts = build_source_contexts(sections, captions, envs)
        self.assertEqual(contexts[caption_id], contexts[figure_id])
        self.assertEqual(contexts[figure_id],
                         "Paper title: Paper {A}\nAuthor abstract: Original abstract.\nSection: Results > Ablation")
        self.assertIn("Section: Results > Latency", contexts["1_2"])
        self.assertNotIn("Results", contexts["2"])
        self.assertEqual(contexts[title_id], "Author abstract: Original abstract.")
        self.assertEqual(contexts[abstract_id], "Paper title: Paper {A}")

    def test_absent_abstract_uses_intro_paragraph_from_merged_section(self):
        sections = [
            {"section": "1+2", "content": "\\section{Overview}Other text.\\section{Introduction}"
             "\\label{sec:intro}<PLACEHOLDER_x_begin>\n\nOriginal opening.\n\nNext paragraph."},
            {"section": "3", "content": r"\section{Results}"},
        ]
        contexts = build_source_contexts(sections, [], [])
        self.assertEqual(contexts["3"], "Introduction excerpt: Original opening.\nSection: Results")
        self.assertNotIn("Introduction excerpt:", contexts["1+2"])

        captions = [{"placeholder": "<PLACEHOLDER_CAP_1>", "content": r"\abstract{Author's words.}"}]
        contexts = build_source_contexts(sections, captions, [])
        self.assertEqual(contexts["3"], "Author abstract: Author's words.\nSection: Results")
