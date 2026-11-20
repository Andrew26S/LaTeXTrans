"""Compare the parts of a translated LaTeX fragment that must survive translation."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import zip_longest
import re

import regex


# Keep offsets into the original fragment for diagnostics. Consume other control
# sequences as whole tokens so escaped backslashes and percent signs stay literal.
_LITERAL = re.compile(
    r"(?P<opening>\\begin\s*\{(?P<env>verbatim\*?|Verbatim\*?|lstlisting\*?|minted\*?|comment)\})"
    r"(?P<body>.*?)(?=\\end\s*\{(?P=env)\}|\Z)"
    r"|\\(?:verb|Verb|lstinline(?:\[[^\]\n]*\])?)(?![A-Za-z@])\*?(?P<delimiter>[^\sA-Za-z*])[^\n]*?(?P=delimiter)"
    r"|(?P<url_open>\\(?:url|nolinkurl|path|href)\s*\{)(?P<url_body>(?:[^{}\\]++|\\[\s\S])*)\}"
    r"|%[^\n]*|\\(?:[A-Za-z@]+|[\s\S])", re.DOTALL,
)
_TOKEN = re.compile(r"\\(?:[A-Za-z@]+|[\s\S])|[{}]|\$\$?")
_PLACEHOLDER = re.compile(r"<PLACEHOLDER_[^<>\n]+>")
_ENV_NAME = re.compile(r"\s*\{([\w.*:-]+)\}")
_STRUCTURAL_COMMANDS = {
    "part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph",
    "item", "bibitem", "title", "caption", "subcaption", "captionof", "keywords", "abstract", "icmltitle", "icmltitlerunning",
}
_KEY_COMMANDS = {
    "label", "ref", "pageref", "eqref", "autoref", "nameref", "cref", "Cref", "vref",
    "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "citeyearpar",
    "nocite", "parencite", "textcite", "autocite", "footcite", "supercite",
    "includegraphics", "input", "include", "bibliography", "bibliographystyle", "url", "nolinkurl", "path",
    "href", "hyperref", "captionof", "documentclass", "bibitem",
}
# Optional citation notes can contain brace groups (including a literal ']').
_ARGUMENT = regex.compile(
    r"(?(DEFINE)(?P<brace>\{(?:[^{}\\]++|\\[\s\S]|(?&brace))*\}))"
    r"\s*\*?\s*(?:\[(?P<note>(?:[^\]{}\\]++|\\[\s\S]|(?&brace))*)\]\s*)*"
    r"(?P<value>(?&brace))"
)


_CODE_NAME = re.compile(r"\s*\{([A-Za-z][A-Za-z0-9_]*(?:(?:\\_)[A-Za-z0-9_]*)*)\}")
_STAR = re.compile(r"\s*\*")


def _targets(name, argument, source):
    keys = argument["value"][1:-1]
    if name in {"url", "nolinkurl", "path", "href"}:
        start, end = argument.span("value")
        keys = source[start + 1:end - 1]
    notes = argument.captures("note")
    if name == "hyperref":
        # Only the optional label is fixed, the mandatory argument is link text.
        if not notes:
            return
        keys, notes = notes[0], []
    citation = "cite" in name
    locators = ()
    if citation:
        # Associate locators with their keys even when the citations move.
        # Prose in a note may be translated. Page numbers must survive.
        locators = tuple(tuple(re.findall(r"[0-9]+", n)) for n in notes)
    multiple_keys = citation or name in {"cref", "Cref", "bibliography"}
    for key in keys.split(",") if multiple_keys else [keys]:
        yield name, key.strip(), locators


def visible_markup(text: str) -> str:
    """Mask comments, code bodies and escaped backslashes without moving offsets."""
    def mask(match):
        value = match[0]
        if match["opening"]:
            return match["opening"] + " " * len(match["body"])
        if match["url_open"]:
            return match["url_open"] + "x" * len(match["url_body"]) + "}"
        if value == r"\\" or value.startswith("%") or match["delimiter"]:
            return " " * len(value)
        return value

    return _LITERAL.sub(mask, text)


def _context(text: str, pos: int) -> str:
    start = max(text.rfind("\n", 0, pos) + 1, pos - 30)
    snippet = text[start:pos + 64].split("\n", 1)[0].strip()
    line = text.count("\n", 0, pos) + 1
    return f"line {line}: {snippet}"


@dataclass
class _Fragment:
    identifiers: dict[str, int] = field(default_factory=dict)
    targets: dict = field(default_factory=lambda: defaultdict(list))
    structure: list = field(default_factory=list)
    open_groups: list = field(default_factory=list)
    extra_closes: list = field(default_factory=list)
    math_boundary: list = field(default_factory=list)
    macros: Counter = field(default_factory=Counter)


def _inspect_command(source, visible, token, result):
    value, pos = token[0], token.start()
    name = value[1:]
    if name in {"begin", "end"}:
        env = _ENV_NAME.match(visible, token.end())
        if env:
            result.structure.append((f"{value}{{{env[1]}}}", pos))
    elif name in _STRUCTURAL_COMMANDS:
        star = "*" if name not in {"item", "bibitem"} and _STAR.match(visible, token.end()) else ""
        result.structure.append((value + star, pos))
    if name in _KEY_COMMANDS:
        argument = _ARGUMENT.match(visible, token.end())
        if argument:
            for target in _targets(name, argument, source):
                result.targets[target].append(pos)
    elif name.isalpha() and name not in _STRUCTURAL_COMMANDS and name not in {"begin", "end", "eg", "ie"}:
        result.macros[value] += 1
        if name == "texttt":
            literal = _CODE_NAME.match(visible, token.end())
            if literal:
                identifier = literal[1].replace(r"\_", "_")
                if "_" in identifier or re.search(r"[a-z][A-Z]", identifier):
                    result.identifiers[identifier] = pos


def _inspect(text: str) -> _Fragment:
    visible = visible_markup(text)
    result = _Fragment()
    for token in _TOKEN.finditer(visible):
        value, pos = token[0], token.start()
        if value == "{":
            result.open_groups.append(pos)
        elif value == "}":
            if result.open_groups:
                result.open_groups.pop()
            else:
                result.extra_closes.append(pos)
        elif value in {"$", "$$", r"\(", r"\)", r"\[", r"\]"}:
            closing = {r"\)": r"\(", r"\]": r"\[", "$": "$", "$$": "$$"}.get(value)
            if value == "$$" and result.math_boundary and result.math_boundary[-1][0] == "$":
                # Adjacent inline formulas: the first dollar closes and the second opens.
                result.math_boundary[-1] = ("$", pos + 1)
            elif result.math_boundary and result.math_boundary[-1][0] == closing:
                result.math_boundary.pop()
            else:
                result.math_boundary.append((value, pos))
        elif value.startswith("\\"):
            _inspect_command(text, visible, token, result)
    return result


def _sequence_error(source, translation, src_text, trans_text, description):
    for expected, actual in zip_longest(source, translation):
        name, pos = expected or ("end of fragment", len(src_text))
        found, location = actual or ("end of fragment", len(trans_text))
        if name != found:
            return (f"Restore {description}. Expected {name} (original {_context(src_text, pos)}), "
                    f"found {found} (translation {_context(trans_text, location)}).")
    return None


def _input_layout(matches):
    boundaries = []
    owners = Counter()
    stack = []
    # The fragment may start inside one or more input files. Each unmatched
    # end marker brings us back to a distinct enclosing file from the source.
    inherited = "file at start of fragment"
    for match in matches:
        tag = match[0]
        if tag.endswith(("_begin>", "_end>")):
            boundaries.append((tag, match.start()))
            if tag.endswith("_begin>"):
                stack.append(tag)
            elif stack:
                stack.pop()
            else:
                inherited = f"file after {tag}"
        else:
            owner = stack[-1] if stack else inherited
            owners[owner, tag] += 1
    return boundaries, owners


def _placeholder_errors(source, translation):
    original = list(_PLACEHOLDER.finditer(source))
    translated = list(_PLACEHOLDER.finditer(translation))
    expected = Counter(m[0] for m in original)
    actual = Counter(m[0] for m in translated)
    errors = []
    for token in sorted(expected.keys() | actual.keys()):
        if expected[token] != actual[token]:
            location = ("original " + _context(source, source.index(token)) if expected[token]
                        else "translation " + _context(translation, translation.index(token)))
            errors.append(f"Preserve {token}: expected {expected[token]}, found {actual[token]} ({location}).")
    if errors:
        return errors

    if not original:
        return []
    # Reconstruction uses literal replacement, even in comments. A token can
    # be present on disk while the corresponding material disappears in TeX.
    old_active = Counter(_PLACEHOLDER.findall(visible_markup(source)))
    new_active = Counter(_PLACEHOLDER.findall(visible_markup(translation)))
    for token in sorted(old_active.keys() | new_active.keys()):
        if old_active[token] != new_active[token]:
            errors.append(f"Keep {token} in its original active or literal context "
                          f"(expected {old_active[token]} active, found {new_active[token]}).")
    old_bounds, old_owners = _input_layout(original)
    new_bounds, new_owners = _input_layout(translated)
    issue = _sequence_error(old_bounds, new_bounds, source, translation, "the order of input-file boundaries")
    if issue:
        return [issue]
    for owner, token in old_owners - new_owners:
        errors.append(f"Restore {token} to its original input file ({owner}).")
    return errors


def validate_fragment(source: str, translation: str) -> tuple[dict[str, str], list[str]]:
    """Return actionable repair messages separately from advisory macro differences."""
    if source == translation:
        return {}, []
    if source.strip() and not translation.strip():
        return {"command_error": "The translation is empty. Restore the translated fragment."}, []
    original = _inspect(source)
    translated = _inspect(translation)
    commands = []
    structure = _sequence_error(original.structure, translated.structure, source, translation,
                                "the original heading, caption and environment structure")
    if structure:
        commands.append(structure)
    for target in sorted(original.targets.keys() | translated.targets.keys()):
        expected = original.targets.get(target, [])
        actual = translated.targets.get(target, [])
        if len(expected) == len(actual):
            continue
        command, key, locators = target
        notes = "".join("[" + ", ".join(numbers or ("note",)) + "]" for numbers in locators)
        action = "Restore" if len(expected) > len(actual) else "Remove unexpected occurrence of"
        detail = f"{action} target {key!r} of \\{command}{notes} (expected {len(expected)}, found {len(actual)})."
        if expected:
            detail += " Original " + _context(source, expected[0])
        if actual:
            detail += " Translation " + _context(translation, actual[0])
        commands.append(detail)

    if original.identifiers:
        # The same identifier can legitimately move out of a font command.
        words = set(re.findall(r"[A-Za-z0-9_]+", translation.replace(r"\_", "_")))
        for name, pos in original.identifiers.items():
            if name not in words:
                commands.append(f"Restore the missing code identifier {name!r} (original {_context(source, pos)}).")

    brackets = []
    # Fragments can start/end inside a group. Preserve inherited boundaries.
    for name, old, new in (("opening", original.open_groups, translated.open_groups),
                           ("closing", original.extra_closes, translated.extra_closes)):
        if len(old) != len(new):
            location = ("translation " + _context(translation, new[-1]) if new
                        else "original " + _context(source, old[-1]))
            brackets.append(f"Unmatched {name} TeX brace: expected {len(old)}, found {len(new)} ({location}).")
    math = _sequence_error(original.math_boundary, translated.math_boundary, source, translation,
                           "the math delimiters at fragment boundaries")
    if math:
        brackets.append(math)

    errors = {}
    for field_name, messages in (("command_error", commands), ("ph_error", _placeholder_errors(source, translation)),
                                 ("bracket_error", brackets)):
        if messages:
            errors[field_name] = "\n".join(messages)
    warnings = [
        f"{macro} ({original.macros[macro]} to {translated.macros[macro]})"
        for macro in sorted(original.macros.keys() | translated.macros.keys())
        if original.macros[macro] != translated.macros[macro]
    ]
    return errors, warnings
