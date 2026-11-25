"""Stage a UQ RDM run read-only into a local APPN-named tree."""

import os
import pathlib
import re
import shutil
import warnings as warn
from typing import Dict

from tqdm import tqdm

from Code.functions.uq_reports.layout import UQRun


# ==================================================================================
def refuse_rdm(path: pathlib.Path, what: str = "output") -> None:
    """Raise if ``path`` is on an RDM drive or a network share.

    Parameters
    ----------
    path : pathlib.Path
        Folder this tool is about to write into.
    what : str, optional
        Label for the error message.

    Raises
    ------
    ValueError
        When ``path`` is on ``R:`` or a UNC (``\\\\server\\share``) path.
    """
    p = pathlib.Path(path).resolve()
    if p.drive.upper() == "R:" or str(p).startswith("\\\\"):
        raise ValueError(f"Refusing to write {what} to RDM/network path {p}. "
                         "Use a local working folder.")


# ==================================================================================
def stage_run(
        run: UQRun,
        work_root: pathlib.Path,
        max_small_mb: float = 5.0,
    ) -> pathlib.Path:
    """Copy one UQ run into ``<work_root>/staging`` under APPN folder names.

    Only reads from the RDM. Copies every file up to ``max_small_mb``, plus
    the large products DS02 and the reports read (reflectance orthos,
    DSM/DTM) and all text/metadata files at any size. Every other large file
    (raw imagery, LiDAR, RGB ortho) gets a zero-byte placeholder so globs and
    presence checks still see it. Re-running skips files already copied.

    Parameters
    ----------
    run : UQRun
        The run to stage.
    work_root : pathlib.Path
        Local working folder (must not be on the RDM).
    max_small_mb : float, optional
        Size limit for copying any file regardless of type.

    Returns
    -------
    pathlib.Path
        The staged run folder (``<work_root>/staging/<node>/<APPN rel path>``).

    Notes
    -----
    ``.gpro`` bundles that are not directly under ``T1_proc`` (nested
    re-processing, bundles parked in ``T0_raw``) are skipped with a warning:
    DS02 never reads them and they can be tens of GB.
    """
    refuse_rdm(work_root, "staging")
    rules = staging_rules()
    dst_root = (pathlib.Path(work_root) / "staging" / run.path.parents[4].name
                / run.appn_rel)
    counts: Dict[str, int] = {"copied": 0, "placeholder": 0, "present": 0}
    max_small = max_small_mb * 1024 * 1024
    print(f"Staging {run.label} -> {dst_root} ...")
    files = list(walk_run(run.path))
    for src, size in tqdm(files, desc=f"stage {run.label}", leave=False):
        rel = src.relative_to(run.path)
        if rules["skip"].search(src.name):
            continue
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if size <= max_small or rules["needed"].search(src.name):
            if dst.exists() and dst.stat().st_size == size:
                counts["present"] += 1
                continue
            shutil.copyfile(src, dst)
            counts["copied"] += 1
        else:
            if not dst.exists():
                dst.touch()
            counts["placeholder"] += 1
    print(f"  {counts['copied']} copied, {counts['placeholder']} placeholders, "
          f"{counts['present']} already present")
    return dst_root


# ==================================================================================
def staging_rules() -> Dict[str, "re.Pattern[str]"]:
    """Filename rules for staging.

    Returns
    -------
    dict
        ``needed``: large files that are always copied; ``skip``: files never
        copied (GDAL side-car caches).
    """
    return {
        "needed": re.compile(
            r"(_(VNIR|SWIR)_Orthomosaic[^/\\]*\.(bin|hdr)$)"
            r"|(_(DTM|DSM)_[^/\\]*\.tif$)"
            r"|(\.(json|ya?ml|txt|hdr|kml|geojson|log|csv|xml)$)", re.IGNORECASE),
        "skip": re.compile(r"\.aux\.xml$", re.IGNORECASE),
    }


# ==================================================================================
def walk_run(run_dir: pathlib.Path):
    """Yield ``(path, size)`` for every file of a run, skipping stray bundles.

    Parameters
    ----------
    run_dir : pathlib.Path
        The ``runNN`` folder.

    Yields
    ------
    tuple of (pathlib.Path, int)
    """
    stack = [pathlib.Path(run_dir)]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                p = pathlib.Path(e.path)
                if e.is_dir(follow_symlinks=False):
                    if p.suffix == ".gpro" and p.parent.name != "T1_proc":
                        warn.warn(f"Skipping {p.relative_to(run_dir)}: a .gpro "
                                  "outside T1_proc is never read by DS02.")
                        continue
                    stack.append(p)
                elif e.is_file(follow_symlinks=False):
                    yield p, e.stat().st_size
