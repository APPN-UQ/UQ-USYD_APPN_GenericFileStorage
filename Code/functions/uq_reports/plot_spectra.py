"""Plot-level spectral signatures via APPN-UQ/GobiExtract_V2."""

import pathlib
import re
import subprocess
import sys
from typing import Any, Dict, List, Tuple

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import yaml

from Code.functions.uq_reports.overview import falsecolour, style_map_axes


# ==================================================================================
def run_gobiextract(
        gobiextract_dir: pathlib.Path,
        hdr: pathlib.Path,
        shp: pathlib.Path,
        out_root: pathlib.Path,
        project_label: str,
        env: Dict[str, str],
    ) -> pathlib.Path:
    """Run GobiExtract_V2's CLI (unmodified) on one VNIR ortho.

    Builds a config from GobiExtract's own ``config.yaml`` with only the
    paths replaced, so its settings (OSAVI/Otsu canopy mask, tolerances)
    stay the repo's.

    Parameters
    ----------
    gobiextract_dir : pathlib.Path
        Clone of github.com/APPN-UQ/GobiExtract_V2.
    hdr : pathlib.Path
        Staged ``*_VNIR_Orthomosaic.hdr``.
    shp : pathlib.Path
        Site plot shapefile.
    out_root : pathlib.Path
        GobiExtract output root (local).
    project_label : str
        GobiExtract ``project_name`` (output sub-folder).
    env : dict
        Environment for the subprocess.

    Returns
    -------
    pathlib.Path
        GobiExtract's per-cube output folder (``data/``, ``plots/``).

    Raises
    ------
    subprocess.CalledProcessError
        When GobiExtract fails.
    """
    gobiextract_dir = pathlib.Path(gobiextract_dir)
    cfg = yaml.safe_load((gobiextract_dir / "config.yaml").read_text(encoding="utf-8"))
    cols = [c for c in gpd.read_file(shp, rows=1).columns if c != "geometry"]
    cfg.update({
        "project_name": project_label,
        "hyperspec_paths": [pathlib.Path(hdr).as_posix()],
        "shp_path": pathlib.Path(shp).as_posix(),
        "out_root": pathlib.Path(out_root).as_posix(),
        "copy_to_buffer": False,
        "id_field": "Plot_ID" if "Plot_ID" in cols else cols[0],
        "id_field2": "fid" if "fid" in cols else None,
    })
    cfg_path = pathlib.Path(out_root) / f"config_{project_label}.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"  GobiExtract on {pathlib.Path(hdr).name} ...")
    subprocess.run([sys.executable, "hyperspec_pipeline.py", "-c", str(cfg_path)],
                   cwd=gobiextract_dir, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    return pathlib.Path(out_root) / project_label / pathlib.Path(hdr).stem


# ==================================================================================
def write_plot_spectra(
        run_dir: pathlib.Path,
        cubes: List[Tuple[pathlib.Path, pathlib.Path]],
        shp: pathlib.Path,
        noisy_nm: float = 920.0,
        blue_floor: float = 0.01,
    ) -> pathlib.Path:
    """Write spectra + plot-map figures and a markdown fragment for a run.

    Parameters
    ----------
    run_dir : pathlib.Path
        Staged run folder.
    cubes : list of (pathlib.Path, pathlib.Path)
        ``(gpro bundle, GobiExtract cube output folder)`` pairs.
    shp : pathlib.Path
        Site plot shapefile.
    noisy_nm : float, optional
        Shade wavelengths above this (Gobi VNIR noise).
    blue_floor : float, optional
        Warn when median 450-500 nm canopy reflectance is below this
        (visible bands clipped to zero by the reflectance calibration).

    Returns
    -------
    pathlib.Path
        ``T1_proc/QC_data/QC_PlotSpectra/QC_PlotSpectra_section.md``.
    """
    matplotlib.use("Agg")
    od = run_dir / "T1_proc" / "QC_data" / "QC_PlotSpectra"
    od.mkdir(parents=True, exist_ok=True)
    plots = gpd.read_file(shp)
    md = ["## Plot-level spectral signatures", "",
          f"Per-plot canopy spectra from GobiExtract_V2 using `{shp.name}` "
          f"({len(plots)} plots): each plot's spectrum is the mean over canopy "
          "pixels (OSAVI with a per-plot Otsu threshold).", ""]
    for gpro, cube_dir in cubes:
        name = cube_dir.name
        spec = load_band_means(cube_dir)
        vi = pd.read_csv(cube_dir / "data" / "veg_indices_from_means.csv")
        fig_spectra(spec, name, od / f"{name}_plot_spectra.png", noisy_nm)
        fig_plot_map(gpro, plots, vi, name, od / f"{name}_plot_map.png")
        md += [f"### {gpro.stem}", "",
               f"![Plot spectra: {spec['n_ok']} of {spec['n']} plots]"
               f"(QC_PlotSpectra/{name}_plot_spectra.png)", "",
               f"![Plot outlines and NDVI](QC_PlotSpectra/{name}_plot_map.png)", ""]
        md += index_table(vi)
        y, wl = spec["Y"], spec["wl"]
        blue = float(np.nanmedian(y[:, (wl >= 450) & (wl <= 500)]))
        if blue < blue_floor:
            md += ["", f"**Caution:** median blue reflectance (450-500 nm) is "
                   f"{blue:.3f}: the visible bands sit at or near zero. That "
                   "inflates NDVI-type indices and makes PRI implausible; check "
                   "the reflectance calibration (ELM) against the panels (QC02) "
                   "before using visible-band indices."]
        md.append("")
    out = od / "QC_PlotSpectra_section.md"
    out.write_text("\n".join(md), encoding="utf-8")
    return out


# ==================================================================================
def load_band_means(cube_dir: pathlib.Path) -> Dict[str, Any]:
    """Load GobiExtract ``band_means.csv`` as a reflectance matrix.

    Parameters
    ----------
    cube_dir : pathlib.Path
        GobiExtract per-cube output folder.

    Returns
    -------
    dict
        ``wl`` (nm), ``Y`` ([plots, bands] reflectance 0-1), ``n``, ``n_ok``.
    """
    bm = pd.read_csv(cube_dir / "data" / "band_means.csv")
    cols = [c for c in bm.columns if re.fullmatch(r"r\d+", c)]
    y = bm[cols].to_numpy(float)
    if np.nanpercentile(y, 99) > 5:
        y = y / 10000.0
    return {"wl": np.array([int(c[1:]) for c in cols], float), "Y": y,
            "n": len(bm), "n_ok": int(np.isfinite(y).any(axis=1).sum())}


# ==================================================================================
def fig_spectra(spec: Dict[str, Any], name: str, out: pathlib.Path,
                noisy_nm: float) -> None:
    """Every plot's spectrum (grey), median and 5-95 % envelope.

    Parameters
    ----------
    spec : dict
        Output of :func:`load_band_means`.
    name : str
        Cube name for the title.
    out : pathlib.Path
        PNG path.
    noisy_nm : float
        Shade wavelengths above this.

    Returns
    -------
    None
    """
    import matplotlib.pyplot as plt
    wl, y = spec["wl"], spec["Y"]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for row in y:
        ax.plot(wl, row, color="0.6", lw=0.5, alpha=0.5)
    lo, hi = np.nanpercentile(y, [5, 95], axis=0)
    ax.fill_between(wl, lo, hi, color="tab:green", alpha=0.25, label="5-95 % of plots")
    ax.plot(wl, np.nanmedian(y, axis=0), color="darkgreen", lw=2, label="median plot")
    ax.axvspan(noisy_nm, wl.max() + 2, color="0.85", alpha=0.6,
               label=f"noisy > {noisy_nm:.0f} nm")
    ax.set_xlim(wl.min(), wl.max())
    ax.set_xlabel("Wavelength (nm)"); ax.set_ylabel("Reflectance")
    ax.set_title(f"Plot-level canopy spectra - {name}\n{spec['n_ok']} of {spec['n']} "
                 "plots (GobiExtract_V2: OSAVI/Otsu canopy mask, band means)",
                 fontsize=10)
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


# ==================================================================================
def fig_plot_map(gpro: pathlib.Path, plots: gpd.GeoDataFrame, vi: pd.DataFrame,
                 name: str, out: pathlib.Path, pad_m: float = 8.0) -> None:
    """Plot outlines over the false colour, and NDVI per plot.

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle (false-colour backdrop and CRS).
    plots : geopandas.GeoDataFrame
        Site plot polygons.
    vi : pandas.DataFrame
        GobiExtract ``veg_indices_from_means.csv``.
    name : str
        Cube name for the title.
    out : pathlib.Path
        PNG path.
    pad_m : float, optional
        Map padding around the plots.

    Returns
    -------
    None
    """
    import matplotlib.pyplot as plt
    idf = "Plot_ID" if "Plot_ID" in plots.columns else plots.columns[0]
    v = vi.assign(_pid=vi["Plot_ID"].astype(str).str.split("_").str[0])
    g = plots.assign(_pid=plots[idf].astype(str)).merge(
        v[["_pid", "NDVI"]], on="_pid", how="left")
    fc = falsecolour(gpro)
    if fc["crs"] is not None and g.crs is not None and g.crs != fc["crs"]:
        g = g.to_crs(fc["crs"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 6.5))
    if fc["rgba"] is not None:
        a1.imshow(fc["rgba"], extent=fc["extent"], interpolation="nearest")
    g.boundary.plot(ax=a1, color="white", lw=0.6)
    a1.set_title("Plot outlines over false-colour VNIR (alignment check)", fontsize=9)
    g.plot(ax=a2, column="NDVI", cmap="RdYlGn", edgecolor="k", lw=0.3, legend=True,
           missing_kwds={"color": "none", "edgecolor": "red"},
           legend_kwds={"label": "Plot NDVI (from mean spectrum)", "shrink": 0.6})
    a2.set_title(f"NDVI per plot ({int(g['NDVI'].notna().sum())} of {len(g)} plots)",
                 fontsize=9)
    x0, y0, x1, y1 = g.total_bounds
    for a in (a1, a2):
        style_map_axes(a)
        a.set_xlim(x0 - pad_m, x1 + pad_m); a.set_ylim(y0 - pad_m, y1 + pad_m)
    fig.suptitle(name, fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


# ==================================================================================
def index_table(vi: pd.DataFrame,
                keep: Tuple[str, ...] = ("NDVI", "GNDVI", "NDVI705", "OSAVI",
                                         "EVI", "PRI", "MTCI")) -> List[str]:
    """Markdown summary table of selected indices across plots.

    Parameters
    ----------
    vi : pandas.DataFrame
        GobiExtract index table.
    keep : tuple of str, optional
        Indices to show.

    Returns
    -------
    list of str
        Markdown lines.
    """
    s = vi[[k for k in keep if k in vi]].describe().T[["mean", "std", "min", "max"]].round(3)
    lines = ["Index summary across plots:", "", "| Index | Mean | SD | Min | Max |",
             "|---|---|---|---|---|"]
    return lines + [f"| {i} | {r['mean']} | {r['std']} | {r['min']} | {r['max']} |"
                    for i, r in s.iterrows()]
