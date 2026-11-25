"""Markdown -> PDF for DS02 reports (pandoc + xelatex)."""

import os
import pathlib
import shutil
import subprocess
from typing import Dict, List, Optional

import pandas as pd
import yaml


# ==================================================================================
def find_tool(name: str, extra: List[str]) -> Optional[str]:
    """Find an executable on PATH, in an env var, or in known install places.

    Parameters
    ----------
    name : str
        Executable name (``pandoc`` / ``xelatex``); the env var checked is
        ``name.upper()`` (``PANDOC`` / ``XELATEX``).
    extra : list of str
        Candidate full paths to try last (``~`` and env vars expanded).

    Returns
    -------
    str or None
    """
    cands = [os.environ.get(name.upper()), shutil.which(name)]
    cands += [os.path.expandvars(os.path.expanduser(e)) for e in extra]
    return next((c for c in cands if c and pathlib.Path(c).is_file()), None)


# ==================================================================================
def md_to_pdf(md: pathlib.Path, text: str, pdf: pathlib.Path,
              margin: str = "1.6cm", mainfont: str = "Calibri") -> pathlib.Path:
    """Render markdown text to PDF with pandoc/xelatex.

    Status emoji are replaced by words (LaTeX fonts lack them) and figures
    are pinned where they appear.

    Parameters
    ----------
    md : pathlib.Path
        Markdown file the text came from (its folder resolves image paths).
    text : str
        Markdown to render.
    pdf : pathlib.Path
        Output PDF.
    margin : str, optional
        Page margin.
    mainfont : str, optional
        Body font (must cover the characters used, e.g. degree signs).

    Returns
    -------
    pathlib.Path
        The PDF.

    Raises
    ------
    FileNotFoundError
        When pandoc or xelatex can't be found (set ``PANDOC`` / ``XELATEX``).
    subprocess.CalledProcessError
        When pandoc fails.
    """
    pandoc = find_tool("pandoc", [
        r"C:\Program Files\RStudio\resources\app\bin\quarto\bin\tools\pandoc.exe",
        r"C:\Program Files\Quarto\bin\tools\pandoc.exe"])
    xelatex = find_tool("xelatex", [
        r"%APPDATA%\TinyTeX\bin\windows\xelatex.exe", "~/.TinyTeX/bin/x86_64-linux/xelatex"])
    if not pandoc or not xelatex:
        raise FileNotFoundError("pandoc and xelatex are needed for PDFs; install "
                                "them or set PANDOC / XELATEX.")
    for glyph, word in {"✅": "PASS", "⚠️": "WARN", "⚠": "WARN", "❌": "FAIL",
                        "➖": "--", "\ufe0f": ""}.items():
        text = text.replace(glyph, word)
    tmp = md.with_name(f"_{md.stem}_pdfsrc.md")
    header = md.with_name("_pdf_header.tex")
    tmp.write_text(text, encoding="utf-8")
    header.write_text("\\usepackage{float}\n\\floatplacement{figure}{H}\n", encoding="utf-8")
    cmd = [pandoc, tmp.name, "-o", str(pdf), f"--pdf-engine={xelatex}",
           f"--resource-path={md.parent}", f"--include-in-header={header.name}",
           "-V", f"geometry:margin={margin}", "-V", f"mainfont={mainfont}",
           "-V", "fontsize=10pt", "-V", "colorlinks=true"]
    try:
        subprocess.run(cmd, cwd=md.parent, check=True, capture_output=True, text=True)
    finally:
        tmp.unlink(missing_ok=True)
        header.unlink(missing_ok=True)
    return pdf


# ==================================================================================
def report_to_pdf(qc_data: pathlib.Path,
                  sections: tuple = ("QC_Overview", "QC_PlotSpectra")) -> pathlib.Path:
    """Render a run's ``QC_report.md`` to ``QC_report.pdf`` with UQ sections.

    The overview and plot-spectra fragments (when present) are spliced in
    ahead of the QC00 section.

    Parameters
    ----------
    qc_data : pathlib.Path
        Run's ``T1_proc/QC_data`` folder.
    sections : tuple of str, optional
        Fragment folders to splice in, in order.

    Returns
    -------
    pathlib.Path
        The PDF.
    """
    md = qc_data / "QC_report.md"
    text = md.read_text(encoding="utf-8")
    extra = "".join((qc_data / s / f"{s}_section.md").read_text(encoding="utf-8") + "\n\n"
                    for s in sections if (qc_data / s / f"{s}_section.md").is_file())
    if extra and "## QC00" in text:
        i = text.index("## QC00")
        text = text[:i] + extra + text[i:]
    return md_to_pdf(md, text, md.with_suffix(".pdf"))


# ==================================================================================
def qa01_markdown(qa01_dir: pathlib.Path) -> pathlib.Path:
    """Assemble a markdown report for one QA01_FlightComparison output folder.

    QA01 writes a summary YAML, CSV tables and figures but no markdown.

    Parameters
    ----------
    qa01_dir : pathlib.Path
        The ``--output-dir`` given to QA01.

    Returns
    -------
    pathlib.Path
        ``<qa01_dir>/QA01_report.md``.
    """
    glyph = {"good": "✅", "acceptable": "✅", "pass": "✅", "warning": "⚠️",
             "warn": "⚠️", "fail": "❌"}
    figs = [("agl_consistency.png", "Flying height above ground (AGL) per run."),
            ("sun_line_angle.png", "Flight-line heading relative to the sun (abeam = BRDF risk)."),
            ("diurnal_coverage.png", "Time of each run relative to solar noon."),
            ("exposure_vs_clearsky.png", "Hyperspectral exposure against clear-sky irradiance."),
            ("photon_dose.png", "Relative photon dose (exposure x irradiance) per run."),
            ("panel_detections.png", "Reflectance panel picks per run."),
            ("height_stability.png", "Within-line height stability.")]
    summ = next(pathlib.Path(qa01_dir).glob("QA01_FlightComparison_*_summary.yaml"))
    s = yaml.safe_load(summ.read_text(encoding="utf-8"))
    sub = summ.parent / s["detail"].split("/")[0]
    comp = pd.read_csv(sub / "acquisition_comparison.csv")
    anom = pd.read_csv(sub / "anomalies.csv")
    lines = [f"# QA01 flight comparison: {s.get('scope', '')}", "",
             f"*{s['script']['name']} {s['script']['version']} · generated "
             f"{s['generated_utc'][:19]} UTC · {s['run']['n_runs']} runs, "
             f"{s['run']['first_run_utc'][:10]} to {s['run']['last_run_utc'][:10]}*", "",
             f"**Overall: {glyph.get(s['status'], '')} {s['status']}**", "", "## Checks", ""]
    lines += md_table(pd.DataFrame(
        [{"Check": k, "Status": f"{glyph.get(v['status'], '')} {v['status']}",
          "Value": v.get("value", ""), "Note": v.get("note", "")}
         for k, v in s["checks"].items()]))
    keep: Dict[str, str] = {
        "run_id": "Run", "n_lines": "Lines", "mean_agl_m": "Mean AGL (m)",
        "mean_solar_elev": "Sun elev (°)", "t2noon_first": "To noon, first (min)",
        "t2noon_last": "To noon, last (min)", "max_abeamness": "Max abeam (°)",
        "settings_exposure_ms": "Exposure set (ms)", "hdr_exposure_ms": "Exposure hdr (ms)",
        "n_panel_detections": "Panel picks"}
    c = comp[[k for k in keep if k in comp]].rename(columns=keep).round(1)
    lines += ["", "## Runs compared", ""] + md_table(c.sort_values("Run"))
    lines += ["", "## Anomalies flagged", ""]
    lines += md_table(anom) if len(anom) else ["None."]
    lines += ["", "## Figures", ""]
    lines += [f"![{cap}]({sub.name}/{f})\n" for f, cap in figs if (sub / f).is_file()]
    out = pathlib.Path(qa01_dir) / "QA01_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ==================================================================================
def md_table(df: pd.DataFrame) -> List[str]:
    """A DataFrame as markdown table lines.

    Parameters
    ----------
    df : pandas.DataFrame

    Returns
    -------
    list of str
    """
    cols = list(df.columns)
    return (["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
            + ["| " + " | ".join(str(r[c]) for c in cols) + " |" for _, r in df.iterrows()])
