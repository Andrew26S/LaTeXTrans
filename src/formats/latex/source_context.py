"""Build translation reference material from the parser's source maps."""

import re

from .utils import get_abstract_pattern, get_command_pattern


_TITLE = get_command_pattern(r"title|icmltitle")
_ABSTRACT_COMMAND = get_command_pattern("abstract")
_ABSTRACT_ENV = get_abstract_pattern()
_HEADING = get_command_pattern(r"section\*?|subsection\*?|subsubsection\*?")
_LEVEL = {"section": 0, "subsection": 1, "subsubsection": 2}
_PART = re.compile(r"<PLACEHOLDER_(?:CAP|ENV)_\d+>")
_INTRO = re.compile(r"\bintroduction\b|引言|绪论|緒論", re.IGNORECASE)


def _excerpt(text):
    text = re.sub(r"<PLACEHOLDER_[^<>]*>", " ", text)
    return " ".join(text.split())


def _first_match(parts, pattern, group):
    for part in parts:
        match = pattern.search(part["content"])
        if match:
            excerpt = _excerpt(match[group])
            if excerpt:
                return part["placeholder"], excerpt
    return None, ""


def build_source_contexts(sections, captions, envs):
    """Index source-only context by section ID or placeholder, including merged sections."""
    title_id, title = _first_match(captions, _TITLE, 4)
    abstract_id, abstract = _first_match(captions, _ABSTRACT_COMMAND, 4)
    if not abstract:
        abstract_id, abstract = _first_match(envs, _ABSTRACT_ENV, 3)

    env_contents = {env["placeholder"]: env["content"] for env in envs}
    paths = {}
    headings = ["", "", ""]
    intro_id, intro = None, ""
    for section in sections:
        section_id = section["section"]
        source = section["content"]
        matches = list(_HEADING.finditer(source)) if section_id not in {"-1", "0"} else []
        section_paths = []
        # Process each heading and the placeholders up to the next heading together.
        for i, heading in enumerate([None, *matches]):
            start = 0
            end = matches[i].start() if i < len(matches) else len(source)
            if heading:
                start = heading.end()
                level = _LEVEL[heading[1].rstrip("*")]
                name = _excerpt(heading[4])
                headings[level:] = [name] + [""] * (2 - level)
                if not abstract and not intro and _INTRO.search(name):
                    body = re.sub(r"\\label\s*\{[^{}]*\}", "", source[start:end])
                    for paragraph in re.split(r"\n\s*\n", body):
                        intro = _excerpt(paragraph)[:800]
                        if intro:
                            intro_id = section_id
                            break

            path = " > ".join(filter(None, headings))
            if path and (heading or not matches):
                section_paths.append(path)
            for ph in _PART.findall(source, start, end):
                paths[ph] = path
                for nested in _PART.findall(env_contents.get(ph, "")):
                    paths[nested] = path
        paths[section_id] = " / ".join(section_paths)

    contexts = {}
    for part, path in paths.items():
        fields = []
        if title and part != title_id:
            fields.append(f"Paper title: {title}")
        if abstract and part != abstract_id:
            fields.append(f"Author abstract: {abstract}")
        if intro and part != intro_id:
            fields.append(f"Introduction excerpt: {intro}")
        if path:
            fields.append(f"Section: {path}")
        if fields:
            contexts[part] = "\n".join(fields)
    return contexts
