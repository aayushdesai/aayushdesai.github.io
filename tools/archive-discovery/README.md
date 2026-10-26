# Archive Discovery — local source-data portal, scientific previews 0.7.1

Enter a source name or ICRS coordinates, inspect archive status and source candidates, then open native product links or preview one selected numerical product. Resolved names require explicit identity confirmation. The compact summary groups spectra, photometry, light curves and images; native measurements are the main visual content.

## Run locally

Use Python 3.9 or newer for the standard-library query service:

```bash
python3 portal_service.py --port 8765
```

Open http://127.0.0.1:8765. Stop the service with Ctrl-C when finished. It binds to loopback only, saves each query locally and makes no request until the user searches. No credentials, bulk download or reduction are supported. This directory contains public source only; private cached examples, native products, environments and science receipts are excluded.

For selected native FITS previews, install the optional scientific runtime with Python 3.12 or newer:

```bash
python3.12 -m venv portal/.runtime
portal/.runtime/bin/python -m pip install -r portal/requirements.txt
```

Scientific rendering also needs LaTeX, dvipng, kpsewhich and the EB Garamond LaTeX package. Project-contained plotting defaults use that font and fail clearly when prerequisites are missing. Metadata and exports remain usable without this optional runtime. No public backend is deployed.

## Evidence and limits

See [coverage](COVERAGE.md). Positions, separation, input epoch and native product relationships are retained. Proper motion and footprint acceptance are not applied. A pointing, source-catalogue candidate or linked product is not a verified detection of the requested target. Query completion is separate from archive completeness. Access failure, unsupported routes, partial results and valid no-match responses stay distinct.

MAST product enumeration is a separate bounded action. A preview reads only the selected HTTPS product with a 10 MiB budget and checked archive hosts/redirects. Oversized or failed responses are retained and not parsed as complete products. Finished previews reuse verified cached bytes. Native errors, quality/mask fields and units are preserved; no fitting, rebinning or new reduction occurs. DESI multi-target arrays require the exact TARGETID; SDSS native identity is checked against SPECOBJ. Four metadata exports retain source and output hashes. The public registry omits private empirical receipts and therefore makes no independently reproducible live-validation claim.

## Pending science and hosting work

Supported wavelength spectra now display F_nu in mJy against Wavelength (angstrom). The conversion uses [Astropy spectral-density equivalencies](https://docs.astropy.org/en/stable/units/equivalencies.html#spectral-flux-and-luminosity-density-units) and propagates each supplied error by the same positive factor. CSV files retain the native arrays, errors, masks and bit flags alongside separate converted columns; preview metadata records both unit systems and the transformation. Negative and zero fluxes remain. Non-positive or non-finite wavelengths are masked only in the derived display. Counts, relative fluxes, surface brightness, time series and integrated X-ray band fluxes retain their native units. This changes units without reducing, resampling, dereddening or shifting spectra. Discovery engine remains 0.7.0; scientific renderer is 0.7.1. No backend or laptop preview server was started for this update.

Future work includes reduction/presentation of products that lack a ready spectrum, remaining releases and surveys, source association/proper-motion/footprint checks, and reviewed hosting. GitHub Pages can serve static files but cannot run this Python service. A live public portal needs a separately reviewed backend or a browser-accessible subset with valid CORS. Do not expose this loopback development service as a public endpoint.

## Offline checks

```bash
python3 -m unittest discover -v
portal/.runtime/bin/python -m unittest test_product_preview -v
python3 coverage_registry.py --validate
```

The distributed tests use synthetic values and temporary directories. Scientific-reader tests requiring optional packages skip clearly in the standard-library environment. No test contacts an archive.
