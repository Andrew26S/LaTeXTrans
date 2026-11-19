"""XeLaTeX text-font compatibility for reconstructed documents."""

# Package hooks also see packages loaded by a class, a style, or an input file.
# Install them before the class loads. Leave math and CJK families to the paper.
_FONT_HOOKS = r"""% LaTeXTrans legacy text fonts
\ifdefined\XeTeXversion
\ExplSyntaxOn
\cs_new_protected:Npn \latextrans_legacy_font:nnNNnn #1#2#3#4#5#6
  {
    % Preserve an OpenType family chosen before this package resets the default.
    \AddToHook{package/#1/before}{
      \cs_if_exist:NT \fontspec_if_fontspec_font:T {
        \group_begin:
        \fontfamily{#3}\selectfont
        \fontspec_if_fontspec_font:T {
          \tl_gset:cx {g_latextrans_#1_#2_tl} {#3}
          \AddToHookNext{package/#1/after}{
            \str_if_eq:eeT {#3} {#2}
              {\tl_set_eq:Nc #3 {g_latextrans_#1_#2_tl}}
          }
        }
        \group_end:
      }
    }
    \AddToHook{package/#1/after}{
      % fontspec resets the defaults if the class loaded the legacy package first.
      \cs_if_exist:NF \setmainfont {
        \AddToHook{package/fontspec/before}{
          \str_if_eq:eeT {#3} {#2} {
            \AddToHook{package/fontspec/after}{
              \exp_args:Nno \clist_if_in:nnT {lmr,lmss,lmtt} {#3}
                {\tl_set:Nn #3 {#2}}
            }
          }
        }
      }
      % Later font declarations win independently for roman, sans, and mono.
      \AddToHook{begindocument/before}{
        \str_if_eq:eeT {#3} {#2} {
          \IfFontExistsTF{TeX~Gyre~#5}
            {#4[#6]{TeX~Gyre~#5}}
            {#4[#6]{\str_case:nn {#2} {
              {ptm}{Latin~Modern~Roman}
              {phv}{Latin~Modern~Sans}
              {pcr}{Latin~Modern~Mono~Light}
            }}}
        }
      }
    }
  }
\cs_new:Npn \latextrans_helvet_scale: {\cs_if_exist_use:cF {Hv@scale} {1}}
\latextrans_legacy_font:nnNNnn {times}   {ptm}\rmdefault\setmainfont{Termes}{}
\latextrans_legacy_font:nnNNnn {mathptmx}{ptm}\rmdefault\setmainfont{Termes}{}
\latextrans_legacy_font:nnNNnn {times}   {phv}\sfdefault\setsansfont{Heros}{Scale=\latextrans_helvet_scale:}
\latextrans_legacy_font:nnNNnn {helvet}  {phv}\sfdefault\setsansfont{Heros}{Scale=\latextrans_helvet_scale:}
\latextrans_legacy_font:nnNNnn {times}   {pcr}\ttdefault\setmonofont{Cursor}{}
\latextrans_legacy_font:nnNNnn {courier} {pcr}\ttdefault\setmonofont{Cursor}{}
\ExplSyntaxOff
\fi
"""


def add_legacy_font_hooks(tex: str) -> str:
    if _FONT_HOOKS in tex:
        return tex
    # Keep leading comments, including TeX editor directives, on the first lines.
    lines = tex.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() and not line.lstrip().startswith("%"):
            lines.insert(index, _FONT_HOOKS)
            return "".join(lines)
    return tex
