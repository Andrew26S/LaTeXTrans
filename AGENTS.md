# Repository Guidelines

## Project Structure

The CLI (`main.py`) and Streamlit interface (`src/gui/`) both call `run_translation` in `src/runtime.py`. Put workflow changes shared by both interfaces there. The coordinator in `src/agents/` delegates parsing, translation, validation, and generation to `tool_agents/`. Edit `src/formats/latex/` for LaTeX-specific behavior such as parsing captions, reconstructing documents, and compiling PDFs.

`terms/` holds terminology CSVs, including subject-specific glossaries such as `cs.AI.csv`. Sample PDFs and comparison images live in `examples/`. Unit tests live in `tests/`, and translation-quality evaluation scripts live in `evaluation/scripts/`.

## Setup and Local Development

Run these commands from the repository root in an activated Python environment.

- `python -m pip install -e .` installs the package, its dependencies, and both command-line entry points in editable mode.
- `cp -n config/default.toml config/local.toml` creates the local configuration on first use. Set the model, API key, and full chat completions URL under `[llm_config]` in the Git-ignored local file.
- `latextrans --project /path/to/paper` translates a local LaTeX project. For PDF output, install the TeX tools and language packages described in `README.md`. Generated projects go under `outputs/` by default.
- `latextrans-gui` launches the Streamlit interface.

## Coding Style

Use four spaces per indentation level, `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_CASE` for constants. Follow the parameter and return type annotations in `src/runtime.py` when extending shared functions. The repository has no configured formatter or linter.

## Testing

Run `python -m unittest discover -s tests` for the full suite. Append `-p 'test_cli.py'` to focus on CLI behavior. Name files `test_<feature>.py` and methods `test_<behavior>`. Use temporary directories for project files and mock external calls in unit tests. There is no configured coverage threshold.

Run the optional TeX smoke test with `python -m unittest discover -s tests/integration` when latexmk and pdfLaTeX are installed.

## Commits and Pull Requests

Use a short imperative commit subject with a type and optional scope, following recent history (for example, `fix(compiler): preserve run artifacts`). In a pull request, describe the behavior change, link any related issue, and list the tests you ran. Include a screenshot or sample PDF when the change affects the GUI or rendered output.
