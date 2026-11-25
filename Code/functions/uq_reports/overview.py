"""Overview figures for a staged run: false-colour VNIR, DSM, trajectory."""

import json
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling


# ==================================================================================
def write_overview(run_dir: pathlib.Path, max_px: int = 1600) -> pathlib.Path:
    """Write overview figures + a markdown fragment for every bundle of a run.

    For each ``T1_proc/*.gpro`` writes into ``T1_proc/QC_data/QC_Overview/``:
    ``<gpro>_vnir_falsecolour.png`` (CIR 800/650/550 nm),
    ``<gpro>_dsm.png`` (LiDAR DSM + hillshade) and ``<gpro>_trajectory.png``
    (GNSS track + hyperspectral flight lines over the ortho), plus
    ``QC_Overview_section.md`` which :func:`report_to_pdf` splices into the
    report.

    Parameters
    ----------
    run_dir : pathlib.Path
        Staged run folder (APPN layout).
    max_px : int, optional
        Longest side of the decimated ortho/DSM reads.

    Returns
    -------
    pathlib.Path
        The markdown fragment.
    """
    matplotlib.use("Agg")
    od = run_dir / "T1_proc" / "QC_data" / "QC_Overview"
    od.mkdir(parents=True, exist_ok=True)
    md = ["## Overview: imagery, surface and flight path", "",
          "False-colour VNIR (near-infrared shown as red, so healthy canopy is "
          "bright red), the LiDAR surface model, and the flown trajectory "
          "with the hyperspectral flight lines.", ""]
    for gpro in sorted((run_dir / "T1_proc").glob("*.gpro")):
        if not (gpro / "mission_data.yaml").exists():
            continue
        s = gpro.stem
        fc = falsecolour(gpro, max_px=max_px)
        fig_falsecolour(gpro, od / f"{s}_vnir_falsecolour.png", fc)
        dsm_note = fig_dsm(gpro, od / f"{s}_dsm.png", max_px=max_px)
        n_lines, has_traj = fig_trajectory(gpro, od / f"{s}_trajectory.png", fc)
        md += [f"### {s}", "",
               f"![VNIR false colour: {fc['note']}](QC_Overview/{s}_vnir_falsecolour.png)", "",
               f"![LiDAR DSM: {dsm_note}](QC_Overview/{s}_dsm.png)", "",
               f"![Trajectory: {n_lines} flight lines"
               f"{'' if has_traj else ', no GNSS trajectory found'}]"
               f"(QC_Overview/{s}_trajectory.png)", ""]
        print(f"  {s}: false colour: {fc['note']}; DSM: {dsm_note}; {n_lines} lines")
    out = od / "QC_Overview_section.md"
    out.write_text("\n".join(md), encoding="utf-8")
    return out


# ==================================================================================
def read_decimated(path: pathlib.Path, bands: List[int], max_px: int
                   ) -> Tuple[np.ndarray, Tuple[float, float, float, float], Any, Any]:
    """Read bands of a raster decimated so the longest side is ``max_px``.

    Parameters
    ----------
    path : pathlib.Path
        Raster (ENVI ``.bin`` or GeoTIFF).
    bands : list of int
        1-based band indexes.
    max_px : int
        Longest output side in pixels.

    Returns
    -------
    tuple
        ``(array[bands, rows, cols] float32, extent (l, r, b, t), crs, nodata)``.
    """
    with rasterio.open(path) as src:
        scale = max(src.width, src.height) / max_px
        shape = (max(1, int(src.height / scale)), max(1, int(src.width / scale)))
        arr = src.read(bands, out_shape=(len(bands),) + shape,
                       resampling=Resampling.nearest).astype("float32")
        b = src.bounds
        return arr, (b.left, b.right, b.bottom, b.top), src.crs, src.nodata


# ==================================================================================
def falsecolour(gpro: pathlib.Path, max_px: int = 1600,
                cir_nm: Tuple[float, float, float] = (800.0, 650.0, 550.0)
                ) -> Dict[str, Any]:
    """Build a CIR false-colour RGBA image from a bundle's VNIR ortho.

    Parameters
    ----------
    gpro : pathlib.Path
        ``.gpro`` bundle.
    max_px : int, optional
        Longest side of the decimated read.
    cir_nm : tuple of float, optional
        NIR, red, green target wavelengths.

    Returns
    -------
    dict
        ``rgba`` (array or None), ``extent``, ``crs``, ``note`` (bands used,
        or why the ortho can't be shown: missing, truncated, blank).
    """
    out: Dict[str, Any] = {"rgba": None, "extent": None, "crs": None, "note": ""}
    bins = sorted((gpro / "products").glob("*_VNIR_Orthomosaic*.bin"))
    if not bins:
        out["note"] = "no VNIR orthomosaic in this bundle"
        return out
    b = bins[0]
    htxt = b.with_suffix(".hdr").read_text(encoding="utf-8", errors="ignore")
    dims = {k.lower(): int(v) for k, v in re.findall(
        r"^(samples|lines|bands)\s*=\s*(\d+)", htxt, re.M | re.I)}
    if len(dims) == 3:
        band_bytes = dims["samples"] * dims["lines"] * 2
        expected, size = band_bytes * dims["bands"], b.stat().st_size
        if size < expected:
            out["note"] = (f"{b.name}: truncated, {size/1e9:.1f} of {expected/1e9:.1f} GB "
                           f"(~{size // band_bytes} of {dims['bands']} bands written)"
                           + ("; header has no wavelengths or georeferencing"
                              if "map info" not in htxt.lower() else ""))
            return out
    m = re.search(r"wavelength\s*=\s*\{([^}]*)\}", htxt, re.IGNORECASE)
    if not m:
        out["note"] = f"{b.name}: header has no wavelength list"
        return out
    wl = np.array([float(v) for v in m.group(1).split(",")])
    idx = [int(np.argmin(np.abs(wl - w))) + 1 for w in cir_nm]
    arr, out["extent"], out["crs"], nod = read_decimated(b, idx, max_px)
    # no-data = every band zero; one zero band is real (dark visible in shade)
    valid = np.any(arr > 0, axis=0)
    if nod is not None and nod != 0:
        valid &= np.any(arr != nod, axis=0)
    if not valid.any():
        out["note"] = f"{b.name}: every pixel is zero (blank ortho)"
        return out
    out["rgba"] = stretch_rgba(arr, valid)
    out["note"] = "bands " + ", ".join(f"{wl[i-1]:.0f} nm" for i in idx)
    return out


# ==================================================================================
def stretch_rgba(arr: np.ndarray, valid: np.ndarray,
                 pct: Tuple[float, float] = (2.0, 98.0)) -> np.ndarray:
    """Percentile-stretch three bands to an RGBA image (alpha = valid).

    Parameters
    ----------
    arr : np.ndarray
        ``[3, rows, cols]`` band stack.
    valid : np.ndarray
        Boolean ``[rows, cols]`` data mask.
    pct : tuple of float, optional
        Low/high percentiles for the stretch.

    Returns
    -------
    np.ndarray
        ``[rows, cols, 4]`` float32 in 0..1.
    """
    out = np.zeros(arr.shape[1:] + (3,), dtype="float32")
    for i in range(3):
        v = arr[i][valid]
        lo, hi = np.percentile(v, pct)
        out[..., i] = np.clip((arr[i] - lo) / max(hi - lo, 1e-6), 0, 1)
    return np.dstack([out, valid.astype("float32")])


# ==================================================================================
def fig_falsecolour(gpro: pathlib.Path, out: pathlib.Path, fc: Dict[str, Any]) -> None:
    """Save the false-colour figure (or a panel saying why it's missing).

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle (for the title).
    out : pathlib.Path
        PNG path.
    fc : dict
        Output of :func:`falsecolour`.

    Returns
    -------
    None
    """
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    if fc["rgba"] is None:
        ax.text(0.5, 0.5, fc["note"], ha="center", va="center", wrap=True,
                transform=ax.transAxes, fontsize=11, color="firebrick")
        ax.set_axis_off()
    else:
        ax.imshow(fc["rgba"], extent=fc["extent"], interpolation="nearest")
        style_map_axes(ax)
    ax.set_title(f"VNIR false colour (CIR) - {gpro.stem}\n{fc['note']}", fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


# ==================================================================================
def fig_dsm(gpro: pathlib.Path, out: pathlib.Path, max_px: int = 1600) -> str:
    """Save the LiDAR DSM with hillshade.

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle.
    out : pathlib.Path
        PNG path.
    max_px : int, optional
        Longest side of the decimated read.

    Returns
    -------
    str
        Short note (file name and elevation range, or ``no DSM``).
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    dsms = [d for d in sorted((gpro / "products").glob("*_DSM_*.tif"))
            if d.stat().st_size > 0]
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    if not dsms:
        ax.text(0.5, 0.5, "no DSM product", ha="center", transform=ax.transAxes)
        ax.set_axis_off(); fig.savefig(out, dpi=150); plt.close(fig)
        return "no DSM"
    arr, ext, _, nod = read_decimated(dsms[0], [1], max_px)
    z = arr[0]
    mask = ~np.isfinite(z) | (z <= -9999)
    if nod is not None:
        mask |= z == nod
    z = np.ma.masked_array(z, mask)
    lo, hi = np.percentile(z.compressed(), [1, 99])
    res = (ext[1] - ext[0]) / z.shape[1]
    shade = LightSource(azdeg=315, altdeg=45).hillshade(
        z.filled(np.median(z.compressed())), vert_exag=3, dx=res, dy=res)
    ax.imshow(np.ma.masked_array(shade, mask), cmap="gray", extent=ext,
              interpolation="nearest")
    im = ax.imshow(z, cmap="terrain", vmin=lo, vmax=hi, alpha=0.65, extent=ext,
                   interpolation="nearest")
    fig.colorbar(im, ax=ax, shrink=0.75).set_label("Elevation (m)")
    style_map_axes(ax)
    ax.set_title(f"LiDAR DSM - {dsms[0].name}\n1-99 % range {lo:.1f} to {hi:.1f} m",
                 fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)
    return f"{dsms[0].name}, {lo:.1f} to {hi:.1f} m"


# ==================================================================================
def fig_trajectory(gpro: pathlib.Path, out: pathlib.Path, fc: Dict[str, Any],
                   thin: int = 20) -> Tuple[int, bool]:
    """Save the GNSS trajectory with hyperspectral flight lines over the ortho.

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle.
    out : pathlib.Path
        PNG path.
    fc : dict
        Output of :func:`falsecolour` (faint backdrop + target CRS).
    thin : int, optional
        Keep every ``thin``-th trajectory vertex.

    Returns
    -------
    tuple of (int, bool)
        Number of flight lines drawn, and whether a trajectory was found.
    """
    import matplotlib.pyplot as plt
    crs = fc["crs"]
    if crs is None:
        dtms = sorted((gpro / "products").glob("*_DTM_*.tif"))
        with rasterio.open(dtms[0]) as src:
            crs = src.crs
    tf = Transformer.from_crs("OGC:CRS84", crs, always_xy=True)
    traj = read_trajectory(gpro, tf, thin)
    lines = flight_lines(gpro)
    fig = plt.figure(figsize=(7.5, 9.5))
    ax = fig.add_axes([0.1, 0.30, 0.85, 0.62])
    if fc["rgba"] is not None:
        ax.imshow(fc["rgba"], extent=fc["extent"], alpha=0.45, interpolation="nearest")
    if traj is not None:
        sc = ax.scatter(traj[0], traj[1], c=traj[2], s=0.3, cmap="viridis")
        fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.01).set_label(
            "GNSS ellipsoidal height (m)")
    for n, pts in lines:
        x, y = tf.transform(pts[:, 0], pts[:, 1])
        ax.plot(x, y, color="crimson", lw=1.6)
        ax.annotate(str(n), (x[0], y[0]), fontsize=6, color="crimson")
    style_map_axes(ax)
    ax.set_title(f"Flight trajectory - {gpro.stem}\nthin = full GNSS track "
                 f"(coloured by height), red = {len(lines)} hyperspec flight lines",
                 fontsize=10)
    ax2 = fig.add_axes([0.1, 0.06, 0.85, 0.16])
    if traj is not None:
        ax2.plot(traj[2], lw=0.8, color="0.3")
        ax2.set_ylabel("Height (m)"); ax2.set_xlabel("Trajectory sample (start to end)")
        ax2.tick_params(labelsize=7)
    fig.savefig(out, dpi=150); plt.close(fig)
    return len(lines), traj is not None


# ==================================================================================
def read_trajectory(gpro: pathlib.Path, tf: Transformer, thin: int
                    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Read the bundle's GNSS trajectory (``SBG/flight_*/trajectory.json``).

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle.
    tf : pyproj.Transformer
        CRS84 -> map CRS transformer.
    thin : int
        Keep every ``thin``-th vertex.

    Returns
    -------
    tuple of np.ndarray or None
        ``(x, y, height)``, or None when there is no usable trajectory.
    """
    traj_f = next(iter(sorted(gpro.glob("SBG/flight_*/trajectory.json"))), None)
    if traj_f is None or traj_f.stat().st_size == 0:
        return None
    feats = json.loads(traj_f.read_text(encoding="utf-8"))["features"]
    ls = [f for f in feats if f["geometry"]["type"] == "LineString"]
    if not ls:
        return None
    c = np.array(ls[0]["geometry"]["coordinates"])[::thin]
    x, y = tf.transform(c[:, 0], c[:, 1])
    return np.asarray(x), np.asarray(y), c[:, 2]


# ==================================================================================
def flight_lines(gpro: pathlib.Path) -> List[Tuple[int, np.ndarray]]:
    """Read hyperspectral flight lines from the bundle's epoch KMLs.

    Parameters
    ----------
    gpro : pathlib.Path
        Bundle.

    Returns
    -------
    list of (int, np.ndarray)
        Line number and ``[n, 2]`` lon/lat vertices.
    """
    lines = []
    for k in sorted(gpro.glob("nHP-*/flight_*/epoch_flightline_*.kml")):
        txt = k.read_text(encoding="utf-8", errors="ignore")
        blocks = re.findall(r"<coordinates>(.*?)</coordinates>", txt, re.S)
        pts = [tuple(map(float, p.split(",")[:2]))
               for blk in blocks for p in blk.split() if "," in p]
        num = re.search(r"(\d+)\.kml$", k.name)
        if len(pts) > 1 and num:
            lines.append((int(num.group(1)), np.array(pts)))
    return lines


# ==================================================================================
def style_map_axes(ax) -> None:
    """Equal aspect, plain metre ticks, axis labels.

    Parameters
    ----------
    ax : matplotlib.axes.Axes

    Returns
    -------
    None
    """
    ax.set_aspect("equal")
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=7)
    ax.set_xlabel("Easting (m)"); ax.set_ylabel("Northing (m)")
