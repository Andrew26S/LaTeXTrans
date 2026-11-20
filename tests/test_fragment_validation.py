import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.agents.tool_agents.translator_agent import TranslatorAgent
from src.agents.tool_agents.validator_agent import ValidatorAgent
from src.formats.latex.utils import sanitize_llm_translation
from src.formats.latex.validation import validate_fragment


class FragmentValidationTests(unittest.TestCase):
    def test_formatting_changes_and_unknown_macros_are_advisory(self):
        for source, translation in [
            (r"\emph{first} and \emph{second}", r"\emph{第一和第二}"),
            (r"\texttt{[}$v$\texttt{]}", r"\texttt{[$v$]}"),
            (r"An \instrument{example}", "一个示例"),
        ]:
            with self.subTest(source=source):
                errors, warnings = validate_fragment(source, translation)
                self.assertEqual(errors, {})
                self.assertTrue(warnings)

    def test_code_identifiers_can_change_fonts_but_must_survive(self):
        for name in ["flushCache", r"cache\_line"]:
            source = r"Run \texttt{" + name + "}."
            self.assertFalse(validate_fragment(source, "执行 " + name)[0])
            self.assertIn("code identifier", validate_fragment(source, "执行另一个函数")[0]["command_error"])

    def test_named_targets_checked_even_if_command_counts_match(self):
        for macro in ["label", "ref", "pageref", "eqref", "cref", "citep", "includegraphics", "href"]:
            with self.subTest(macro=macro):
                source = "Previous line\nSee \\" + macro + "{alpha}{here}."
                errors, _ = validate_fragment(source, source.replace("alpha", "beta"))
                message = errors["command_error"]
                for expected in ["alpha", "beta", "line 2"]:
                    self.assertIn(expected, message)

    def test_percent_encoded_url_is_one_literal_target(self):
        for command in [r"\url", r"\href"]:
            source = command + r"{https://test.invalid/a%20b}{title}"
            translation = source.replace("title", "标题")
            self.assertFalse(validate_fragment(source, translation)[0])
            self.assertIn("a%20b", validate_fragment(source, translation.replace("20", "23"))[0]["command_error"])

    def test_ref_prefix_does_not_hide_missing_reference(self):
        errors, _ = validate_fragment(r"\ref{x}", r"\refstepcounter{x}")
        self.assertIn("'x'", errors["command_error"])

    def test_unbraced_inputs_are_compared_by_filename(self):
        for filename, argument in [("chapters/results.tex", "chapters/results.tex"),
                                   ("results table.tex", '"results table".tex')]:
            with self.subTest(filename=filename):
                source = "See below.\n\\input " + argument + "\n"
                self.assertFalse(validate_fragment(source, r"见下文。\input{" + filename + "}")[0])
                self.assertFalse(validate_fragment(source, r'见下文。\input{"' + filename + '"}')[0])
                for replacement in ("见下文。", r"见下文。\input wrong"):
                    message = validate_fragment(source, replacement)[0]["command_error"]
                    self.assertIn(filename, message)
                    self.assertIn("line 2", message)

    def test_unparsed_protected_command_cannot_disappear(self):
        for command in (r"\input\filename", r"\ref\labelname"):
            source = "See " + command
            self.assertFalse(validate_fragment(source, "见 " + command)[0])
            self.assertIn("expected 1, found 0", validate_fragment(source, "见下文。")[0]["command_error"])

    def test_citations_can_move_and_regroup_with_their_notes(self):
        source = r"See \citep[see {also}][p.~17]{a,b} and \citep{c}."
        translation = r"\citep{c}、\citep[另见][第17页]{b} 和 \citep[参见][第17页]{a}。"
        self.assertFalse(validate_fragment(source, translation)[0])
        self.assertIn("command_error", validate_fragment(source, translation.replace("17", "19", 1))[0])
        self.assertIn("command_error", validate_fragment(source, translation.replace("{b}", "{a}"))[0])

    def test_keep_hyperref_label_and_allow_translated_link_text(self):
        source = r"\hyperref[sec:a]{see section A}"
        self.assertFalse(validate_fragment(source, r"\hyperref[sec:a]{参见 A 节}")[0])
        self.assertIn("sec:a", validate_fragment(source, r"\hyperref[sec:b]{参见 B 节}")[0]["command_error"])

    def test_compares_heading_levels_and_environment_nesting(self):
        for source, translation in [
            (r"\subsection*{Findings}", r"\section*{结果}"),
            (r"\section*{Findings}", r"\section{结果}"),
            (r"\begin{quote}\begin{itemize}\end{itemize}\end{quote}",
             r"\begin{quote}\begin{itemize}\end{quote}\end{itemize}"),
            (r"\captionof{figure}{cat}", r"\captionof{table}{猫}"),
        ]:
            with self.subTest(source=source):
                self.assertIn("command_error", validate_fragment(source, translation)[0])

    def test_tex_group_and_math_boundaries(self):
        for source, translation in [
            ("Domain", r"区间 $[0,1)$ 与字面量 \{"),
            (r"end} here {start", r"结束} 这里 {开始"),
            (r"$a$$b$", r"\(a\) \(b\)"),
            ("literal", r"\verb|{ [ ) $|"),
        ]:
            with self.subTest(translation=translation):
                self.assertFalse(validate_fragment(source, translation)[0])
        for translation in [r"\emph{坏", r"坏}", r"\(x$", "$x"]:
            with self.subTest(translation=translation):
                self.assertIn("bracket_error", validate_fragment("text", translation)[0])

    def test_comments_and_code_do_not_introduce_live_markup(self):
        for literal in [r"\verb!\ref{bad} \end{document} {!", r"\lstinline|\end{document} $|",
                        r"% \end{document}", r"\\end{document}"]:
            with self.subTest(literal=literal):
                self.assertFalse(validate_fragment("old", literal)[0])
                self.assertEqual(sanitize_llm_translation("old", literal), literal)
        source = r"\begin{verbatim}x\end{verbatim}"
        code = source.replace("x", r"\end{document} \ref{x} {")
        self.assertFalse(validate_fragment(source, code)[0])
        self.assertEqual(sanitize_llm_translation(source, code + r"\end{document}"), code)

    def test_placeholders_keep_multiplicity_and_file_ownership(self):
        begin, end = "<PLACEHOLDER_a_begin>", "<PLACEHOLDER_a_end>"
        env, cap, macro = "<PLACEHOLDER_ENV_1>", "<PLACEHOLDER_CAP_2>", "<PLACEHOLDER_NEWCOMMAND_0>"
        source = begin + env + cap + macro + end
        self.assertFalse(validate_fragment(source, begin + cap + macro + env + end)[0])
        self.assertFalse(validate_fragment(begin + env, "前文" + begin + env)[0])
        self.assertFalse(validate_fragment(env + begin + end, begin + end + env)[0])
        for translation in [source + env, source.replace(macro, ""), source.replace(env, "% " + env + "\n"), end + env + cap + macro + begin,
                            env + begin + cap + macro + end]:
            with self.subTest(translation=translation):
                self.assertIn("ph_error", validate_fragment(source, translation)[0])


class ValidationWorkflowTests(unittest.TestCase):
    def test_repair_only_actionable_parts_then_clear_saved_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sections = [
                {"section": "1", "content": r"\textbf{Description}", "trans_content": "描述"},
                {"section": "2", "content": r"See \ref{a}.", "trans_content": r"见 \ref{b}。"},
            ]
            for name, value in [("sections", sections), ("envs", []), ("captions", [])]:
                (root / f"{name}_map.json").write_text(json.dumps(value))
            validator = ValidatorAgent({}, directory, directory)
            report = validator.execute()
            self.assertEqual([item["num_or_ph"] for item in report], ["2"])
            sections[1]["trans_content"] = r"见 \ref{a}。"
            (root / "sections_map.json").write_text(json.dumps(sections))
            self.assertEqual(validator.execute(report), [])
            self.assertEqual(json.loads((root / "errors_report.json").read_text()), [])

    def test_many_simultaneous_repairs_receive_their_diagnostics(self):
        agent = TranslatorAgent(config={"llm_config": {}})
        sections = [{"section": str(i)} for i in range(24)]
        agent.errors_report = [{"part": "sec", "num_or_ph": part["section"], "command_error": "fix " + part["section"]}
                               for part in sections]

        async def repair(part, session, error_message):
            await asyncio.sleep(0)
            return {**part, "trans_content": error_message}

        with patch.object(agent, "_translate_section", new=AsyncMock(side_effect=repair)):
            asyncio.run(asyncio.wait_for(agent._retranslate_error_parts(sections, [], [], None), timeout=2))
        self.assertTrue(all(part["trans_content"] == "fix " + part["section"] for part in sections))
