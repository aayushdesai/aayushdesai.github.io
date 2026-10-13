# Coverage and scientific limits

This source checkpoint supplies callable local routes rather than a claim of all holdings or measurements. Public source excludes private control receipts. Release and access behaviour must be checked in the environment where it is used.

| Facility / survey | Callable scope | Remaining limits |
|---|---|---|
| MAST: HST, JWST, Swift and other returned collections | CAOM observation cone with bounded paging; separate product listing; selected supported numerical FITS preview | Product enumeration is separate; mission/footprint completeness, proper motion and target detection unverified |
| SDSS | DR17 optical SpecObjAll spectrum metadata and individual FITS links; separate SDSS-V DR19 targets | Repeats/sky/QA retained; APOGEE, MaNGA and later optical releases not searched |
| DESI | DR1 iron HEALPix coadd metadata, exact TARGETID and native bundle links | Exposures, nightly/tile coadds and other releases absent; large bundles can exceed preview budget |
| LAMOST | DR12 v2.0 LRS/MRS; explicit older branch choices | MRS components are distinct from parent files; JSON query completion unconfirmed; no general photometry enumeration |
| XMM-Newton | 5XMM-DR15 catalogue energy flux bands/errors plus separate pointing log | Stacked intervals are not single epochs; native spectra/light curves and model-dependent flux conversion remain separate |
| Chandra | Pinned CSC2.1 CLI aperture fluxes/bounds plus pointing log | CLI lacks completion marker; CSC2.2 and native extraction products absent |
| Keck | Selected LRIS, DEIMOS and ESI raw-table cones | Always partial facility scope; reduced products, other instruments and verified file links unresolved; an overflowing empty response is not a negative result |
| GTC | Explicit blocked/unsupported state | No working target query or native product route |
| Gemini | Canonical file metadata route | Current tested network had HTTP403 IP login requirement; no bypass; paging/partitions unverified |
| ESO | Reduced ObsCore product metadata and links | Bounded controls timed out; raw holdings and footprint acceptance absent |
| Gaia, Pan-STARRS, ZTF, WISE, 2MASS, ASAS-SN, ATLAS, SkyMapper | Registry documentation | No callable local catalogue/time-series adapters in this release |

Native values and errors retain their units and provenance. Catalogue total bands overlap sub-bands; they are not independent quantities to sum. Unspecified confidence stays unknown. A small value is not silently converted to an upper limit. SDSS plots exclude non-positive inverse variance/non-finite points; native AND/OR bits remain available for quality review. Generic FITS QUALITY/DQ flags are retained. Numerical previews require declared units or a verified release-specific data model; images/events are not treated as measured flux series.

Primary documentation: [MAST](https://mast.stsci.edu/api/v0/), [SDSS spectral units](https://www.sdss4.org/dr17/spectro/spectro_basics/), [DESI access](https://data.desi.lbl.gov/doc/access/), [LAMOST](https://www.lamost.org/dr12/v2.0/doc/query-introduction), [CSC2.1 CLI](https://cxc.cfa.harvard.edu/csc2.1/cli/index.html), [Keck TAP](https://koa.ipac.caltech.edu/UserGuide/PyKOA/TAPClients.html), [Gemini API](https://archive.gemini.edu/help/api.html), [ESO](https://archive.eso.org/programmatic/).
