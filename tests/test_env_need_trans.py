import unittest

from src.formats.latex.parser import LatexParser
from src.formats.latex.utils import env_has_natural_language, env_need_trans


class EnvNeedTransTests(unittest.TestCase):
    def assertTranslated(self, env_name, content):
        self.assertTrue(env_need_trans(env_name, content), content)

    def assertNotTranslated(self, env_name, content):
        self.assertFalse(env_need_trans(env_name, content), content)

    def test_text_environments_are_translated(self):
        self.assertTranslated("theorem", r"\begin{theorem}[Convergence] Let $f$ be convex. Then \[ x_t \to x^* \] holds.\end{theorem}")
        self.assertTranslated("lemma", r"\begin{lemma}\label{lem:a} For all $n \ge 1$, the bound holds.\end{lemma}")
        self.assertTranslated("definition", r"\begin{definition} A graph is connected if every pair of vertices is joined.\end{definition}")
        self.assertTranslated("enumerate", r"\begin{enumerate} \item We propose X. \item It is fast. \end{enumerate}")
        self.assertTranslated("description", r"\begin{description} \item[CLIP] dual encoder \end{description}")
        self.assertTranslated("quote", r"\begin{quote} Attention is all you need. \end{quote}")
        self.assertTranslated("IEEEkeywords", r"\begin{IEEEkeywords} multimodal retrieval, embeddings \end{IEEEkeywords}")
        self.assertTranslated("minipage", r"\begin{minipage}{0.5\textwidth} Results on <PLACEHOLDER_ENV_3> show gains. \end{minipage}")

    def test_text_with_nested_math_environment_is_translated(self):
        self.assertTranslated("remark", r"\begin{remark} Note that \begin{equation} a=b \end{equation} implies the claim. \end{remark}")

    def test_cjk_source_text_is_translated(self):
        self.assertTrue(env_has_natural_language(r"\begin{note} 这是一个定理。\end{note}"))

    def test_abstract_is_always_translated(self):
        self.assertTranslated("abstract", r"\begin{abstract}\end{abstract}")

    def test_non_text_environments_are_not_translated(self):
        self.assertNotTranslated("array", r"\begin{array}{cc} a & b \end{array}")
        self.assertNotTranslated("pmatrix", r"\begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}")
        self.assertNotTranslated("eqnarray", r"\begin{eqnarray} x &=& y \end{eqnarray}")
        self.assertNotTranslated("spacing", r"\begin{spacing}{0.9} \tableofcontents \end{spacing}")
        self.assertNotTranslated("center", r"\begin{center} $x^2 + y^2 = z^2$ \end{center}")
        self.assertNotTranslated("enumerate", r"\begin{enumerate} \item $a$ \item $b$ \end{enumerate}")
        self.assertNotTranslated("mybox", r"\begin{mybox} <PLACEHOLDER_ENV_4> \end{mybox}")

    def test_macro_definition_bodies_are_not_translated(self):
        self.assertNotTranslated("list", r"\begin{list}{}{\setlength{\leftmargin}{#1}} \item[] } {\end{list}")
        self.assertNotTranslated("array", r"\begin{array}{l} #1 \end{array}")
        self.assertNotTranslated("itemize", r"\begin{itemize} \item Option #1 enabled \end{itemize}")

    def test_escaped_hash_is_not_a_macro_parameter(self):
        self.assertTranslated("itemize", r"\begin{itemize} \item Issue \#1 was fixed quickly. \end{itemize}")

    def test_environment_with_caption_is_not_translated(self):
        self.assertNotTranslated("minipage", r"\begin{minipage}{\linewidth} Some text <PLACEHOLDER_CAP_2> \end{minipage}")

    def test_parser_sets_need_trans_without_llm(self):
        parser = LatexParser(".", ".")
        result = parser._extract_envs(
            r"Intro \begin{theorem} Every bounded sequence converges. \end{theorem} and \begin{pmatrix} 1 \end{pmatrix}."
        )
        self.assertEqual(result, "Intro <PLACEHOLDER_ENV_1> and <PLACEHOLDER_ENV_2>.")
        self.assertEqual([e["need_trans"] for e in parser.envs_json], [True, False])


if __name__ == "__main__":
    unittest.main()
