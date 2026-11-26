# UQ_Reports — DS02 QC + PDF reports for the UQ RDM layout

UQ-node addition to this fork (APPN-UQ). It runs the DS02 QC scripts on
flights stored in the **UQ RDM layout**, adds a few report figures, and
renders one PDF per run plus a cross-run QA01 PDF per project.

## Why it's needed

The UQ RDM (created by ProjectManageAPP) is laid out as

```
R:\APPF_Data-Q6940\<project>\<site>_F|_C\<Sensor>\<YYYY-MM-DD>\runNN\{T0_raw,T1_proc,T2_traits}
R:\APPF_Data-Q6940\<project>\<site>_F|_C\Documents\Shapefile\plotFile.shp
```

`parse_APPN_dataset_path` rejects that: it wants a year-prefixed site folder
(`2025MQN24-32`, not `MQN24-32_F`) and upper-case sensors (`GOBI`, not
`Gobi`), and every DS02 script skips runs it can't parse. DS02 also writes
its outputs into the run folder. So instead of touching the RDM, this tool
**stages each run read-only into a local folder under APPN names** and runs
everything there. It refuses to write to `R:` or a network path.

Staging copies all metadata/text files, the reflectance orthos and the
DSM/DTM; other large files (raw imagery, LiDAR clouds, RGB ortho) become
zero-byte placeholders. `.gpro` bundles outside `T1_proc` (nested
re-processing, bundles parked in `T0_raw`) are skipped with a warning.
Re-running skips files already staged.

## What a report contains

Per run (`QC_report.pdf`):
- the DS02 overview table, **QC01** (flight/acquisition) and **QC03**
  (raster validity) sections, as rendered by `Code/functions/qc_report`;
- **Overview** per `.gpro`: false-colour VNIR (800/650/550 nm), LiDAR DSM
  with hillshade, GNSS trajectory with the hyperspectral flight lines;
- **Plot-level spectral signatures** (when the site has
  `Documents/Shapefile/*.shp` and `--gobiextract` is given): per-plot canopy
  spectra from [GobiExtract_V2](https://github.com/APPN-UQ/GobiExtract_V2)
  (run unmodified, OSAVI/Otsu canopy mask), plot outlines over the false
  colour (alignment check), NDVI per plot, an index table, and a caution
  when the visible bands sit at zero (calibration problem).

Per project: `QA01_<project>.pdf` (cross-run acquisition comparison; runs
with open QC findings are included, as these reports are for review).

QC00 (GCPs) and QC02 (panel spectra) need digitised `QC_GCP*` / `QC_ELM*`
layers in `T1_proc/QC_data/` and aren't run here.

## Run

From the repo root, in the `datastorage` conda env (plus `scikit-image` for
GobiExtract), with `pandoc` and `xelatex` available (RStudio/Quarto's pandoc
and TinyTeX are found automatically; otherwise set `PANDOC` / `XELATEX`):

```bash
conda activate datastorage
python Code/UQ_Reports/UR00_UQGobiReports.py \
    --path "R:\APPF_Data-Q6940\2026_SijeshNatarajan_E_SugarCaneCCS_SRA\MQN24-32_F" \
    --work "C:\scratch\DS02_reports" \
    --dates 2025 \
    --gobiextract "C:\code\appn\GobiExtract_V2"
```

`--path` can be any level (project, site, sensor, date or run) and takes
several paths. `--to-rdm` also copies each run's PDF to
`<run>\T1_proc\QC_reports\` on the RDM (only that file is written).
Other options: `--sensor` (default `Gobi`), `--allow-multi-gpro` (QC03 on runs
with several bundles), `--skip-qa01`, `--max-small-mb`.

## Outputs (all under `--work`)

| Folder | Contents |
|---|---|
| `staging/<node>/<project>/<year><site>/<SENSOR>/<date>/<run>/T1_proc/QC_data/` | DS02 outputs, `QC_Overview/`, `QC_PlotSpectra/`, `QC_report.md` + `.pdf` |
| `reports/<project>/` | `<site>_<sensor>_<date>_<run>_QC_report.pdf`, `QA01_<project>.pdf` |
| `reports/index.csv` | one row per run: QC01/QC03 status, PDF path, notes |
| `QA01/<project>/` | QA01 tables and figures |
| `gobiextract/` | GobiExtract configs and outputs (`band_means.csv`, indices, masks) |
| `logs/` | one log per DS02 script per run |

Without `--to-rdm` nothing is written to the RDM.

## Code

- `Code/UQ_Reports/UR00_UQGobiReports.py` — entry script.
- `Code/functions/uq_reports/` — `layout` (find/parse UQ runs, site
  shapefile), `staging` (read-only staging, RDM guard), `overview` (figures),
  `plot_spectra` (GobiExtract + spectra figures), `pdf` (pandoc/xelatex,
  QA01 markdown).

Author: Daniel Smith (UQ), 2026-11.
