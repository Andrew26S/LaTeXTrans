"""XeLaTeX text-font compatibility for reconstructed documents."""

import re

# Install before the class so font choices made in .cls and .sty files are visible.
_FONT_HOOKS = r"""% LaTeXTrans legacy text fonts
\ifdefined\XeTeXversion
\ExplSyntaxOn
\bool_new:N \l_latextrans_mapped_font_bool
\cs_new_protected:Npn \latextrans_map_family:NNnnnn #1#2#3#4#5#6
  {
    % fontspec resets the text families when first loaded, even if the class
    % already selected a legacy family. Preserve that choice for the final check.
    \AddToHook{package/fontspec/before}{
      \str_if_eq:eeT {#1} {#3} {
        \AddToHook{package/fontspec/after}{
          \exp_args:Nno \clist_if_in:nnT {lmr,lmss,lmtt} {#1}
            {\tl_set:Nn #1 {#3}}
        }
      }
    }
    % Record the resolved family, including options such as BoldFont and Scale.
    % A later style can reset the NFSS default without loading a font package.
    \AddToHook{package/fontspec/after}{
      \ExpandArgs{c}\NewCommandCopy {latextrans_set_#3:} #2
      \RenewDocumentCommand #2 {O{} m O{}} {
        \use:c {latextrans_set_#3:} [##1]{##2}[##3]
        \tl_set_eq:cN {latextrans_explicit_#3:} #1
      }
    }
    \AddToHook{begindocument/before}{
      \str_if_eq:eeT {#1} {#3} {
        \bool_set_true:N \l_latextrans_mapped_font_bool
        \cs_if_exist:cTF {latextrans_explicit_#3:}
          {\tl_set_eq:Nc #1 {latextrans_explicit_#3:}}
          {
            \IfFontExistsTF{TeX~Gyre~#4}
              {#2[#6]{TeX~Gyre~#4}}
              {#2[#6]{Latin~Modern~#5}}
          }
      }
    }
  }
\cs_new:Npn \latextrans_helvet_scale: {\cs_if_exist_use:cF {Hv@scale} {1}}
\latextrans_map_family:NNnnnn \rmdefault\setmainfont {ptm}{Termes}{Roman}{}
\latextrans_map_family:NNnnnn \sfdefault\setsansfont {phv}{Heros}{Sans}{Scale=\latextrans_helvet_scale:}
\latextrans_map_family:NNnnnn \ttdefault\setmonofont {pcr}{Cursor}{Mono~Light}{}
% A late fontenc package may have selected T1 after the explicit OpenType font.
% Use its Unicode encoding for normal text and symbols such as the list bullet.
\AddToHook{begindocument/before}{
  \bool_if:NT \l_latextrans_mapped_font_bool {
    \cs_if_exist:cT {\UTFencname/\familydefault/m/n}
      {\tl_set_eq:NN \encodingdefault \UTFencname}
  }
}
\ExplSyntaxOff
\fi
"""


# The parser strips comments when an already translated project is used as input.
_GENERATED_HOOKS = re.compile(
    r"^(?:% LaTeXTrans legacy text fonts\n)?\\ifdefined\\XeTeXversion\s+\\ExplSyntaxOn\s+"
    r"(?:\\cs_new_protected:Npn\s+\\latextrans_legacy_font:|\\bool_new:N\s+\\l_latextrans_mapped_font_bool\b)"
    r".*?^\\ExplSyntaxOff\s+\\fi\b[ \t]*\n?", re.MULTILINE | re.DOTALL,
)


def add_legacy_font_hooks(tex: str) -> str:
    previous = _GENERATED_HOOKS.search(tex)
    if previous:
        return tex[:previous.start()] + _FONT_HOOKS + tex[previous.end():]
    # Keep leading comments, including TeX editor directives, on the first lines.
    lines = tex.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() and not line.lstrip().startswith("%"):
            lines.insert(index, _FONT_HOOKS)
            return "".join(lines)
    return tex
