"""Run DS02 QC on UQ RDM flights and build PDF reports with UQ extras.

Point ``--path`` at any level of a UQ project on the RDM (project, site,
sensor, date or run folder). For every run found this script:

1. stages the run read-only into ``<work>/staging`` under the APPN folder
   names DS02 needs (``2025MQN24-32/GOBI`` for ``MQN24-32_F/Gobi``);
2. runs QC01_FlightCheck and QC03_RasterCheck on the staged copy;
3. writes overview figures (false-colour VNIR, LiDAR DSM, GNSS trajectory
   with flight lines);
4. if the site has ``Documents/Shapefile/*.shp`` and ``--gobiextract`` is
   given, extracts per-plot canopy spectra with APPN-UQ/GobiExtract_V2 and
   plots them (spectra, plot map, NDVI, index table);
5. renders ``QC_report.pdf`` per run;

then runs QA01_FlightComparison per project and renders its PDF. All PDFs
are collected in ``<work>/reports/<project>/``. Nothing is ever written to
the RDM: ``--work`` must be a local folder.

Command-line Arguments
----------------------
--path : str
    Any level of a UQ project tree on the RDM.
--work : str
    Local working folder (staging copies, QC outputs, reports).
--sensor : str (one or more)
    Sensor folders to include (default: Gobi).
--dates : str (one or more)
    Keep runs whose date starts with these (e.g. 2025, 2025-07).
--gobiextract : str
    Path to a GobiExtract_V2 clone; omit to skip plot spectra.
--allow-multi-gpro : flag
    Pass through to QC03 for runs with more than one .gpro.
--skip-qa01 : flag
    Don't run the cross-run QA01 comparison.
--max-small-mb : float
    Copy any file up to this size when staging (default 5).
"""

# ==============================================================================

__title__ = "UQ Gobi QC reports"
__author__ = "Daniel Smith"
__version__ = "v1.0(25.11.2026)"
__email__ = "daniel.smith3@uq.edu.au"

# ==============================================================================
# ========== Import core packages ==========
import os
import sys
import argparse
import pathlib
import shutil
import subprocess
from typing import Dict, List

# ========== Import other packages ==========
import git
from git import exc as git_exc
import pandas as pd
import yaml
from tqdm import tqdm
import warnings as warn

# ========== Resolve git root (must happen before importing Code.functions.*) ==========
try:
    _git_root = git.Repo(os.getcwd(), search_parent_directories=True
                         ).git.rev_parse("--show-toplevel")
except git_exc.InvalidGitRepositoryError as err:
    raise git_exc.InvalidGitRepositoryError(
        f"Script must be run from inside a git repo (cwd={os.getcwd()})."
    ) from err
if _git_root not in sys.path:
    sys.path.insert(0, _git_root)

# ========== Import custom packages ==========
import Code.functions.uq_reports as ur
from Code.functions.uq_reports.overview import falsecolour


# ==================================================================================
def main(args: argparse.Namespace) -> None:
    """Find runs, stage them, run QC + extras, render reports.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments.

    Returns
    -------
    None
    """
    # ========== Setup ==========
    work = pathlib.Path(args.work).resolve()
    ur.refuse_rdm(work, "the working folder")
    env = subprocess_env()
    os.environ.update(env)   # this process plots too (numpy BLAS DLLs, GDAL_DATA)
    runs = ur.find_uq_runs(pathlib.Path(args.path), args.sensor, args.dates)
    print(f"Found {len(runs)} run(s) under {args.path}")

    # ========== Per run ==========
    rows = [process_run(run, work, env, args) for run in tqdm(runs, desc="runs")]

    # ========== Per project: QA01 ==========
    if not args.skip_qa01:
        for project in sorted({r.project for r in runs}):
            run_qa01(project, runs, work, env)

    # ========== Index ==========
    write_index(rows, work)


# ==================================================================================
def process_run(run: "ur.UQRun", work: pathlib.Path, env: Dict[str, str],
                args: argparse.Namespace) -> Dict[str, str]:
    """Stage one run, run QC01/QC03 and the extras, render its PDF.

    Parameters
    ----------
    run : UQRun
        Run to process.
    work : pathlib.Path
        Local working folder.
    env : dict
        Environment for subprocesses.
    args : argparse.Namespace
        Parsed CLI arguments.

    Returns
    -------
    dict
        Index row: label, RDM path, QC statuses, PDF path, notes.
    """
    staged = ur.stage_run(run, work, args.max_small_mb)
    logs = work / "logs"
    run_ds02("QC01_FlightCheck", staged.parent, env, logs / f"QC01_{run.label}.log")
    extra = ["--allow-multi-gpro"] if args.allow_multi_gpro else []
    run_ds02("QC03_RasterCheck", staged.parent, env, logs / f"QC03_{run.label}.log", extra)
    qc_data = staged / "T1_proc" / "QC_data"
    if not (qc_data / "QC_report.md").is_file():
        warn.warn(f"{run.label}: no QC_report.md (no processed .gpro in T1_proc?) - skipped.")
        return {"run": run.label, "rdm_path": str(run.path), "pdf": "",
                "note": "no processed bundle"}
    ur.write_overview(staged)
    note = add_plot_spectra(run, staged, work, env, args.gobiextract)
    pdf = ur.report_to_pdf(qc_data)
    dest = work / "reports" / run.project / f"{run.label}_QC_report.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, dest)
    return {"run": run.label, "rdm_path": str(run.path), **qc_statuses(qc_data),
            "pdf": str(dest), "note": note}


# ==================================================================================
def run_ds02(script: str, path: pathlib.Path, env: Dict[str, str],
             log: pathlib.Path, extra: List[str] = ()) -> int:
    """Run one DS02 script on a staged folder, logging to a file.

    Parameters
    ----------
    script : str
        Script stem in ``Code/DS02_DatasetQA``.
    path : pathlib.Path
        ``--path`` for the script.
    env : dict
        Subprocess environment.
    log : pathlib.Path
        Log file.
    extra : list of str, optional
        Extra CLI arguments.

    Returns
    -------
    int
        Exit code (non-zero is warned about, not raised: QC status lives in
        the report, and one bad run shouldn't stop the batch).
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"  {script} ...")
    with open(log, "w", encoding="utf-8") as fh:
        rc = subprocess.run([sys.executable, f"Code/DS02_DatasetQA/{script}.py",
                             "--path", str(path), *extra],
                            env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc:
        warn.warn(f"{script} exited {rc} on {path} - see {log}")
    return rc


# ==================================================================================
def add_plot_spectra(run: "ur.UQRun", staged: pathlib.Path, work: pathlib.Path,
                     env: Dict[str, str], gobiextract: str) -> str:
    """Run GobiExtract on each readable VNIR ortho and write the spectra section.

    Parameters
    ----------
    run : UQRun
        The run (for the site shapefile).
    staged : pathlib.Path
        Staged run folder.
    work : pathlib.Path
        Local working folder.
    env : dict
        Subprocess environment.
    gobiextract : str or None
        GobiExtract_V2 clone, or None to skip.

    Returns
    -------
    str
        Note for the index (why spectra were skipped, or empty).
    """
    shp = ur.site_shapefile(run)
    if gobiextract is None:
        return "plot spectra skipped (no --gobiextract)"
    if shp is None:
        return f"plot spectra skipped ({run.site_folder} has no Documents/Shapefile)"
    cubes = []
    for gpro in sorted((staged / "T1_proc").glob("*.gpro")):
        if falsecolour(gpro)["rgba"] is None:
            continue
        hdr = next(gpro.glob("products/*_VNIR_Orthomosaic.hdr"))
        cubes.append((gpro, ur.run_gobiextract(
            pathlib.Path(gobiextract), hdr, shp, work / "gobiextract", run.label, env)))
    if not cubes:
        return "plot spectra skipped (no readable VNIR ortho)"
    ur.write_plot_spectra(staged, cubes, shp)
    return ""


# ==================================================================================
def run_qa01(project: str, runs: List["ur.UQRun"], work: pathlib.Path,
             env: Dict[str, str]) -> None:
    """Run QA01 over a project's staged runs and render its PDF.

    Includes runs with open (untriaged) QC findings, since these reports are
    for review. Uses a short ``--output-dir`` to stay under Windows' path
    length limit.

    Parameters
    ----------
    project : str
        Project folder name.
    runs : list of UQRun
        All runs (filtered to the project).
    work : pathlib.Path
        Local working folder.
    env : dict
        Subprocess environment.

    Returns
    -------
    None
    """
    node = next(r for r in runs if r.project == project).path.parents[4].name
    out = work / "QA01" / project
    rc = run_ds02("QA01_FlightComparison", work / "staging" / node / project, env,
                  work / "logs" / f"QA01_{project}.log",
                  ["--output-dir", str(out), "--include-runs", "untriaged"])
    if rc:
        return
    md = ur.qa01_markdown(out)
    pdf = ur.md_to_pdf(md, md.read_text(encoding="utf-8"), md.with_suffix(".pdf"))
    dest = work / "reports" / project / f"QA01_{project}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, dest)


# ==================================================================================
def qc_statuses(qc_data: pathlib.Path) -> Dict[str, str]:
    """Overall status of each DS02 script from its summary YAML.

    Parameters
    ----------
    qc_data : pathlib.Path
        Run's ``T1_proc/QC_data``.

    Returns
    -------
    dict
        ``{"QC01_FlightCheck": "pass", ...}``.
    """
    return {s.name.replace("_summary.yaml", ""):
            yaml.safe_load(s.read_text(encoding="utf-8")).get("status", "")
            for s in sorted(qc_data.glob("QC0*_summary.yaml"))}


# ==================================================================================
def write_index(rows: List[Dict[str, str]], work: pathlib.Path) -> None:
    """Write and print a one-line-per-run index of statuses and PDFs.

    Parameters
    ----------
    rows : list of dict
        Output of :func:`process_run`.
    work : pathlib.Path
        Local working folder.

    Returns
    -------
    None
    """
    df = pd.DataFrame(rows)
    out = work / "reports" / "index.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(df.drop(columns=["rdm_path"], errors="ignore").to_string(index=False))
    print(f"Index: {out}")


# ==================================================================================
def subprocess_env() -> Dict[str, str]:
    """Environment for DS02/GobiExtract subprocesses.

    Disables GDAL side-car files (so nothing is written next to source
    rasters), forces UTF-8 and a non-GUI matplotlib backend, and puts this
    conda env's DLL folders on PATH (needed on Windows when the env's python
    is called without ``conda activate``).

    Returns
    -------
    dict
    """
    env = dict(os.environ, GDAL_PAM_ENABLED="NO", PYTHONUTF8="1", MPLBACKEND="Agg")
    prefix = pathlib.Path(sys.prefix)
    dll_dirs = [prefix, prefix / "Library" / "mingw-w64" / "bin",
                prefix / "Library" / "usr" / "bin", prefix / "Library" / "bin",
                prefix / "Scripts"]
    env["PATH"] = os.pathsep.join([str(d) for d in dll_dirs if d.is_dir()]
                                  + [env.get("PATH", "")])
    for var, sub in (("GDAL_DATA", "gdal"), ("PROJ_LIB", "proj")):
        d = prefix / "Library" / "share" / sub
        if d.is_dir():
            env.setdefault(var, str(d))
    return env


# ==================================================================================
if __name__ == "__main__":
    # ========== chdir to git root (resolved at module top) ==========
    os.chdir(_git_root)

    # ========== Parse args ==========
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--path", required=True,
                        help="Any level of a UQ project tree on the RDM.")
    parser.add_argument("--work", required=True,
                        help="Local working folder (never on the RDM).")
    parser.add_argument("--sensor", nargs="+", default=["Gobi"],
                        help="Sensor folders to include (default: Gobi).")
    parser.add_argument("--dates", nargs="+", default=None,
                        help="Keep runs whose date starts with these (e.g. 2025 2025-07).")
    parser.add_argument("--gobiextract", default=None,
                        help="GobiExtract_V2 clone for plot spectra (omit to skip).")
    parser.add_argument("--allow-multi-gpro", action="store_true",
                        help="Pass through to QC03 for runs with several .gpro bundles.")
    parser.add_argument("--skip-qa01", action="store_true",
                        help="Don't run the cross-run QA01 comparison.")
    parser.add_argument("--max-small-mb", type=float, default=5.0,
                        help="Copy any file up to this size when staging (default 5).")
    main(parser.parse_args())
