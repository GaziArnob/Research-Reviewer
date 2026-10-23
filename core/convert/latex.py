import re

from core.convert.model import Block, Doc
from core.convert.templates import PaperTemplate

_SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#", "_": r"\_", "%": r"\%",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_UNICODE = {"\u2264": r"$\leq$", "\u2265": r"$\geq$", "\u2248": r"$\approx$", "\u2260": r"$\neq$", "\u00b1": r"$\pm$",
            "\u00d7": r"$\times$", "\u2192": r"$\rightarrow$", "\u2190": r"$\leftarrow$", "\u2208": r"$\in$",
            "\u03b1": r"$\alpha$", "\u03b2": r"$\beta$", "\u03b3": r"$\gamma$", "\u03b4": r"$\delta$", "\u03b5": r"$\epsilon$",
            "\u03f5": r"$\epsilon$", "\u03b6": r"$\zeta$", "\u03b7": r"$\eta$", "\u03b8": r"$\theta$", "\u03ba": r"$\kappa$",
            "\u03bb": r"$\lambda$", "\u03bc": r"$\mu$", "\u03bd": r"$\nu$", "\u03c0": r"$\pi$", "\u03c1": r"$\rho$",
            "\u03c3": r"$\sigma$", "\u03c4": r"$\tau$", "\u03c6": r"$\phi$", "\u03c7": r"$\chi$", "\u03c8": r"$\psi$",
            "\u03c9": r"$\omega$", "\u0394": r"$\Delta$", "\u0398": r"$\Theta$", "\u03a3": r"$\Sigma$", "\u03a9": r"$\Omega$",
            "\u2211": r"$\sum$", "\u221e": r"$\infty$", "\u00b7": r"$\cdot$", "\u2207": r"$\nabla$", "\u2202": r"$\partial$",
            "\u221a": r"$\surd$", "\u2282": r"$\subset$", "\u00b0": r"\textdegree{}", "\u2022": r"\textbullet{}",
            "\u2205": r"$\emptyset$", "\u27e8": r"$\langle$", "\u27e9": r"$\rangle$", "\u2113": r"$\ell$", "\u2200": r"$\forall$",
            "\u2203": r"$\exists$", "\u2229": r"$\cap$", "\u222a": r"$\cup$", "\u22c5": r"$\cdot$", "\u2217": r"$\ast$",
            "\u2020": r"\dag{}", "\u2021": r"\ddag{}", "\u2261": r"$\equiv$", "\u221d": r"$\propto$", "\u22a5": r"$\perp$",
            "\u2286": r"$\subseteq$", "\u2295": r"$\oplus$", "\u2297": r"$\otimes$", "\u21d2": r"$\Rightarrow$", "\u2194": r"$\leftrightarrow$",
            "\u2212": "-", "\u2013": "--", "\u2014": "---", "\u201c": "``", "\u201d": "''", "\u2018": "`", "\u2019": "'",
            "\u2026": r"\ldots{}", "\u00a0": "~", "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl"}
_CITE = re.compile(r"\[(\d+(?:\s*[,\u2013\u2014-]\s*\d+)*)\]")


def esc(text: str) -> str:
    return "".join(_SPECIAL.get(c) or _UNICODE.get(c) or c for c in text)


def non_ascii_left(text: str) -> int:
    return sum(1 for c in text if ord(c) > 127)


def _expand(spec: str, valid: set[int]) -> list[int] | None:
    nums: list[int] = []
    for part in re.split(r"\s*,\s*", spec):
        rng = re.split(r"\s*[\u2013\u2014-]\s*", part)
        if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit() and 0 <= int(rng[1]) - int(rng[0]) < 40:
            nums += list(range(int(rng[0]), int(rng[1]) + 1))
        elif part.strip().isdigit():
            nums.append(int(part))
        else:
            return None
    return nums if nums and all(n in valid for n in nums) else None


def text_with_cites(text: str, valid: set[int]) -> tuple[str, int]:
    """Escape text and turn numeric citations like [3, 5] into \\cite{r3,r5}. Returns (latex, number of citations)."""
    out, pos, n = [], 0, 0
    for m in _CITE.finditer(text):
        nums = _expand(m.group(1), valid)
        if not nums:
            continue
        out.append(esc(text[pos:m.start()]).rstrip() + "~")
        out.append("\\cite{" + ",".join(f"r{k}" for k in nums) + "}")
        pos, n = m.end(), n + 1
    out.append(esc(text[pos:]))
    return "".join(out), n


def _front(doc: Doc, tpl: PaperTemplate, anonymous: bool) -> list[str]:
    style = tpl.latex.author_style
    title = esc(doc.title or "Title")
    kws = ", ".join(esc(k) for k in doc.keywords)
    authors = " \\\\ ".join(esc(a) for a in doc.authors_raw[:6]) or "Author names"
    abstract = ["\\begin{abstract}", esc(doc.abstract), "\\end{abstract}"]
    L: list[str] = []
    if style == "ieee":
        who = "\\IEEEauthorblockN{Anonymous Authors}" if anonymous else \
            f"\\IEEEauthorblockN{{{authors}}}\n\\IEEEauthorblockA{{\\textit{{TODO: affiliations}}}}"
        L += [f"\\title{{{title}}}", f"\\author{{{who}}}", "\\maketitle"] + abstract
        if kws:
            L += ["\\begin{IEEEkeywords}", kws, "\\end{IEEEkeywords}"]
    elif style == "icml":
        L += ["\\twocolumn[", f"\\icmltitle{{{title}}}", "\\begin{icmlauthorlist}",
              "\\icmlauthor{Anonymous Authors}{anon}" if anonymous else f"\\icmlauthor{{{authors}}}{{aff}}", "\\end{icmlauthorlist}",
              "\\icmlaffiliation{" + ("anon" if anonymous else "aff") + "}{Anonymous Institution}",
              "\\icmlcorrespondingauthor{Anonymous}{anon@example.com}", f"\\icmlkeywords{{{kws}}}", "\\vskip 0.3in", "]",
              "\\printAffiliationsAndNotice{}"] + abstract
    elif style == "acmart":
        L += [f"\\title{{{title}}}", "\\author{Anonymous}" if anonymous else f"\\author{{{authors}}}",
              "\\affiliation{\\institution{Institution}\\city{City}\\country{Country}}"] + abstract + \
             [f"\\keywords{{{kws}}}" if kws else "% \\keywords{...}", "\\maketitle"]
    else:
        who = {"neurips": "Anonymous Author(s)", "iclr": "Anonymous authors\\\\Paper under double-blind review",
               "acl": "Anonymous ACL submission", "cvpr": "Anonymous CVPR submission\\\\Paper ID \\paperID",
               "aaai": "Anonymous submission"}.get(style, "Anonymous Authors")
        L += [f"\\title{{{title}}}", f"\\author{{{who if anonymous else authors}}}", "\\maketitle"] + abstract
    return L


def _block(b: Block, valid: set[int], stat: dict) -> list[str]:
    if b.kind == "para":
        s, n = text_with_cites(b.text, valid)
        stat["citations"] += n
        return [s, ""]
    if b.kind == "bullets":
        items = []
        for it in b.items:
            s, n = text_with_cites(it, valid)
            stat["citations"] += n
            items.append(f"  \\item {s}")
        return ["\\begin{itemize}"] + items + ["\\end{itemize}", ""]
    cap, n = text_with_cites(b.caption, valid)
    stat["citations"] += n
    env = "figure" if b.kind == "figure" else "table"
    label = f"{'fig' if b.kind == 'figure' else 'tab'}:{b.num}"
    if b.kind == "figure" and b.image:
        body = [f"  \\includegraphics[width=\\linewidth]{{images/{b.image}}}  % matched automatically: check it is the right figure"]
        stat["figures_with_image"] += 1
    else:
        what = "figure" if b.kind == "figure" else "table"
        body = [f"  \\fbox{{\\parbox{{0.9\\linewidth}}{{\\centering TODO: insert {what} {b.num or ''}}}}}  % not recovered from the PDF"]
    return [f"\\begin{{{env}}}[t]", "  \\centering"] + body + [f"  \\caption{{{cap}}}", f"  \\label{{{label}}}", f"\\end{{{env}}}", ""]


def render_latex(doc: Doc, tpl: PaperTemplate, anonymous: bool) -> tuple[str, dict]:
    stat = {"citations": 0, "figures_with_image": 0}
    opts = ",".join(tpl.latex.options)
    L = [f"% Generated by Research Reviewer for: {tpl.name}",
         "% NOT guaranteed to compile: see CONVERSION_REPORT.md. Download the venue's class/style file first.",
         f"\\documentclass[{opts}]{{{tpl.latex.documentclass}}}" if opts else f"\\documentclass{{{tpl.latex.documentclass}}}"]
    L += tpl.latex.preamble
    if tpl.latex.author_style == "cvpr":
        L += ["\\def\\paperID{****}", "\\def\\confName{CVPR}", "\\def\\confYear{2025}"]
    L += ["", "\\begin{document}", ""]
    if doc.footnotes:
        L += ["% Author footnotes found in the PDF (move them where the venue wants them):"] + \
             ["% " + doc.footnotes[i:i + 90].replace("\n", " ") for i in range(0, len(doc.footnotes), 90)] + [""]
    L += _front(doc, tpl, anonymous) + [""]
    valid = {int(k) for k, _ in doc.references if k.isdigit()}
    cmd = {1: "section", 2: "subsection", 3: "subsubsection"}
    for s in doc.sections:
        L.append(f"\\{cmd[s.level]}{{{esc(s.heading)}}}")
        for b in s.blocks:
            L += _block(b, valid, stat)
    if doc.acknowledgements and not anonymous:
        L.append("\\section*{Acknowledgments}")
        for b in doc.acknowledgements:
            L += _block(b, valid, stat)
    if doc.references:
        L += ["\\begin{thebibliography}{99}"]
        for key, raw in doc.references:
            L.append(f"\\bibitem{{r{key}}} {esc(raw)}" if key.isdigit() else f"\\bibitem{{{key}}} {esc(raw)}")
        L += ["\\end{thebibliography}"]
    L += ["", "\\end{document}", ""]
    tex = "\n".join(L)
    stat["non_ascii_left"] = non_ascii_left(tex)
    return tex, stat
