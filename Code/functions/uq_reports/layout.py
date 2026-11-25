"""Find and parse runs in the UQ RDM folder layout."""

import pathlib
import re
import warnings as warn
from dataclasses import dataclass
from typing import List, Optional


# ==================================================================================
@dataclass(frozen=True)
class UQRun:
    """One run folder in the UQ RDM layout.

    Attributes
    ----------
    path : pathlib.Path
        The ``runNN`` folder.
    project : str
        Project folder name (``YYYY_Name_E|I_Topic_Org``).
    site_folder : str
        Site folder name as on the RDM (e.g. ``MQN24-32_F``).
    sensor_folder : str
        Sensor folder name as on the RDM (e.g. ``Gobi``).
    date : str
        Date folder name (``YYYY-MM-DD`` or ``YYYYMMDD``).
    run : str
        Run folder name (e.g. ``run00``).
    """

    path: pathlib.Path
    project: str
    site_folder: str
    sensor_folder: str
    date: str
    run: str

    @property
    def site(self) -> str:
        """Site name without the ``_F`` (field) / ``_C`` (controlled) suffix."""
        return re.sub(r"_(F|C)$", "", self.site_folder)

    @property
    def site_dir(self) -> pathlib.Path:
        """The site folder on the RDM."""
        return self.path.parents[2]

    @property
    def appn_rel(self) -> pathlib.Path:
        """Relative path of this run in the APPN layout DS02 expects.

        Site gets the flight year as prefix (``2025MQN24-32``) and the sensor
        is upper-cased (``GOBI``), which is what ``parse_APPN_dataset_path``
        accepts.
        """
        year = self.date[:4]
        return (pathlib.Path(self.project) / f"{year}{self.site}"
                / self.sensor_folder.upper() / self.date / self.run)

    @property
    def label(self) -> str:
        """Short label for file names, e.g. ``MQN24-32_Gobi_2025-07-16_run00``."""
        return f"{self.site}_{self.sensor_folder}_{self.date}_{self.run}"


# ==================================================================================
def parse_uq_run(run_dir: pathlib.Path) -> Optional[UQRun]:
    """Parse a run folder in the UQ layout.

    Parameters
    ----------
    run_dir : pathlib.Path
        Candidate ``<project>/<site>/<sensor>/<date>/runNN`` folder.

    Returns
    -------
    UQRun or None
        None when the folder does not look like a UQ run (name is not
        ``runNN``, parent is not a date, or there is no ``T1_proc``).
    """
    run_re = re.compile(r"^run_?\d+$", re.IGNORECASE)
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$")
    run_dir = pathlib.Path(run_dir)
    if not (run_re.match(run_dir.name) and date_re.match(run_dir.parent.name)
            and (run_dir / "T1_proc").is_dir() and len(run_dir.parents) >= 4):
        return None
    if not re.search(r"_(F|C)$", run_dir.parents[2].name):
        warn.warn(f"{run_dir.parents[2].name}: site folder has no _F/_C suffix "
                  "- not the usual UQ layout; continuing.")
    return UQRun(path=run_dir, project=run_dir.parents[3].name,
                 site_folder=run_dir.parents[2].name,
                 sensor_folder=run_dir.parents[1].name,
                 date=run_dir.parent.name, run=run_dir.name)


# ==================================================================================
def find_uq_runs(
        path: pathlib.Path,
        sensors: Optional[List[str]] = None,
        dates: Optional[List[str]] = None,
    ) -> List[UQRun]:
    """Find every run at or below ``path`` (project, site, sensor, date or run).

    Parameters
    ----------
    path : pathlib.Path
        Any level of a UQ project tree.
    sensors : list of str, optional
        Keep only these sensor folders (case-insensitive), e.g. ``["Gobi"]``.
    dates : list of str, optional
        Keep only runs whose date folder starts with one of these strings
        (``"2025"`` = all of 2025, ``"2025-07"`` = July 2025).

    Returns
    -------
    list of UQRun
        Sorted by site, sensor, date, run.

    Notes
    -----
    The walk never descends into tier folders or ``.gpro``/``.graw``
    bundles, so it is fast even on the RDM.
    """
    path = pathlib.Path(path)
    keep_sensors = {s.lower() for s in sensors} if sensors else None
    found = []
    stack = [path]
    while stack:
        d = stack.pop()
        run = parse_uq_run(d)
        if run is not None:
            found.append(run)
            continue
        if re.match(r"^T\d+_", d.name) or d.suffix in (".gpro", ".graw"):
            continue
        stack.extend(p for p in d.iterdir() if p.is_dir())
    if keep_sensors:
        found = [r for r in found if r.sensor_folder.lower() in keep_sensors]
    if dates:
        found = [r for r in found if any(r.date.startswith(x) for x in dates)]
    return sorted(found, key=lambda r: (r.site, r.sensor_folder, r.date, r.run))


# ==================================================================================
def site_shapefile(run: UQRun) -> Optional[pathlib.Path]:
    """Return the site's plot shapefile, ``<site>/Documents/Shapefile/*.shp``.

    Parameters
    ----------
    run : UQRun
        Any run of the site.

    Returns
    -------
    pathlib.Path or None
        The shapefile (``plotFile.shp`` preferred when there are several), or
        None when the site has none.
    """
    shps = sorted((run.site_dir / "Documents" / "Shapefile").glob("*.shp"))
    if not shps:
        return None
    preferred = [s for s in shps if s.stem.lower() == "plotfile"]
    if len(shps) > 1 and not preferred:
        warn.warn(f"{run.site_folder}: several shapefiles, using {shps[0].name}")
    return (preferred or shps)[0]
