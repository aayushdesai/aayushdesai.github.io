"""SDSS DR17 optical spectra and DESI DR1 HEALPix-coadd discovery through TAP.

Metadata only: native links can identify a multi-target file, not a downloaded
or association-validated spectrum. Uses exact text IDs from VOTable TABLEDATA.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from checkpoint_io import MAX_BYTES, fetch, new_attempt_stem, utcnow
from lamost_metadata import _target, _number, _identifier, _separation
from tap_metadata import parse_votable

ENDPOINT = "https://datalab.noirlab.edu/tap/sync"
CONFIG = {
    "sdssspectra": dict(table="sdss_dr17.specobjall", release="DR17",
        columns=("specobjid", "ra", "dec", "plate", "mjd", "fiberid", "run2d", "instrument", "survey", "scienceprimary", "class", "z"),
        ra="ra", dec="dec", coverage="SDSS DR17 SpecObjAll optical spectrum metadata; includes repeats, QA and sky entries; APOGEE, MaNGA and SDSS-V not searched"),
    "desi": dict(table="desi_dr1.zpix", release="DR1 iron",
        columns=("id", "targetid", "mean_fiber_ra", "mean_fiber_dec", "survey", "program", "healpix", "mean_mjd", "coadd_exptime"),
        ra="mean_fiber_ra", dec="mean_fiber_dec", coverage="DESI DR1 iron HEALPix coadds by mean fibre position; individual exposures, nightly/tile coadds and other releases not enumerated")}


def query_url(archive, target, limit=200):
    if archive not in CONFIG:
        raise ValueError("Spectral archive must be sdssspectra or desi.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("Record limit must be an integer from 1 to 10000.")
    target = _target(target)
    config = CONFIG[archive]
    query = (f"SELECT TOP {limit + 1} " + ",".join(config["columns"]) +
        f" FROM {config['table']} WHERE 't'=q3c_radial_query({config['ra']},{config['dec']},"
        f"{target['ra_deg']:.16g},{target['dec_deg']:.16g},{target['radius_arcsec']/3600:.16g})")
    params = dict(REQUEST="doQuery", LANG="ADQL", FORMAT="votable", MAXREC=limit + 1, QUERY=query)
    return ENDPOINT + "?" + urlencode(params), params


def native_product(archive, row):
    """Construct only the documented release paths; unsupported reductions lack links."""
    if archive == "sdssspectra":
        plate = _identifier(row.get("plate"), "plate", True, True)
        mjd = _identifier(row.get("mjd"), "mjd", True, True)
        fibre = _identifier(row.get("fiberid"), "fiberid", True, True)
        version = row.get("run2d")
        if version in ("26", "103", "104"):
            branch = f"sdss/spectro/redux/{version}/spectra"
        elif version == "v5_13_2":
            branch = "eboss/spectro/redux/v5_13_2/spectra/full"
        else:
            return None
        filename = f"spec-{int(plate):04d}-{mjd}-{int(fibre):04d}.fits"
        return f"https://dr17.sdss.org/sas/dr17/{branch}/{int(plate):04d}/{filename}"
    if archive == "desi":
        pixel = _identifier(row.get("healpix"), "healpix", True, True)
        survey, program = row.get("survey"), row.get("program")
        if survey not in ("cmx", "main", "special", "sv1", "sv2", "sv3") or program not in ("backup", "bright", "dark", "other"):
            raise ValueError("Unexpected DESI survey or programme path component.")
        if not 0 <= int(pixel) < 12 * 64 ** 2:
            raise ValueError("Invalid DESI NSIDE=64 HEALPix.")
        return (f"https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/{survey}/{program}/"
                f"{int(pixel)//100}/{pixel}/coadd-{survey}-{program}-{pixel}.fits")
    raise ValueError("Unsupported spectral archive.")


def normalise(archive, rows, target):
    target = _target(target)
    config = CONFIG[archive]
    records, identities = [], set()
    for original in rows:
        row = dict(original)
        ra = _number(row.get(config["ra"]), config["ra"], True)
        dec = _number(row.get(config["dec"]), config["dec"], True)
        if not 0 <= ra < 360 or not -90 <= dec <= 90:
            raise ValueError("Invalid spectrum position.")
        if archive == "sdssspectra":
            identity = _identifier(row.get("specobjid"), "specobjid", True, True)
            for key in ("specobjid", "plate", "mjd", "fiberid"):
                row[key] = _identifier(row.get(key), key, True, True)
            instrument, exposure, epoch = row.get("instrument"), None, None
            night_mjd = _number(row.get("mjd"), "mjd")
            relation = "native_individual_coadded_spectrum"
        else:
            identity = _identifier(row.get("id"), "id", True, True)
            for key in ("id", "targetid", "healpix"):
                row[key] = _identifier(row.get(key), key, True, True)
            instrument = "DESI"
            exposure = _number(row.get("coadd_exptime"), "coadd_exptime")
            if exposure is not None and exposure < 0:
                raise ValueError("Negative coadd exposure time.")
            epoch, night_mjd = _number(row.get("mean_mjd"), "mean_mjd"), None
            relation = "target_row_in_native_multitarget_coadd"
        if identity in identities:
            raise ValueError("Duplicate spectrum catalogue identity.")
        identities.add(identity)
        separation = _separation(target["ra_deg"], target["dec_deg"], ra, dec)
        records.append(dict(archive=archive, record_id=config["release"] + ":" + identity,
            record_kind="observation_product", product_type="spectrum", observation_id=identity,
            data_release=config["release"], collection=config["table"], instrument=instrument,
            facility="SDSS" if archive == "sdssspectra" else "DESI Mayall",
            product_url=native_product(archive, row), product_access_format="application/fits",
            product_relation=relation, product_access_verified=False,
            native_target_id=row.get("targetid"), native_reduction=row.get("run2d") if archive == "sdssspectra" else "iron",
            ra_deg=ra, dec_deg=dec, observation_mjd=epoch, archive_night_mjd=night_mjd,
            observed_at=None, exposure_seconds=exposure, has_been_observed=True,
            separation_arcsec=separation, within_requested_cone=separation <= target["radius_arcsec"] + 1e-6,
            match_method="spectrum_fibre_position_cone", association_status="candidate_not_verified",
            footprint_verified=False, query_position=target, native_metadata=row))
    return records


def search_spectra(archive, target, out, timeout=20, limit=200, fetcher=fetch):
    target = _target(target)
    timeout = _number(timeout, "timeout", True)
    if not 0 < timeout <= 120:
        raise ValueError("Timeout must be > 0 and <= 120 seconds.")
    url, params = query_url(archive, target, limit)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    attempt = new_attempt_stem(out, archive)
    result = dict(archive=archive, status="unavailable", records=[], query_complete=False,
        response_parsed=False, started_at_utc=utcnow(), query_url=url, query_parameters=params,
        coverage=CONFIG[archive]["coverage"], target=target, returned_count=0, retained_count=0,
        warnings=["Spectrum metadata and fibre positions provide candidates; identity, proper motion and aperture acceptance are unverified.",
                  "Product URLs are documented routes; file access and native spectrum arrays are not verified by this query."])
    try:
        status, headers, body = fetcher(url, timeout)
        if not isinstance(body, bytes):
            raise ValueError("Fetcher must return bytes.")
        raw = out / (attempt + ".response")
        with raw.open("xb") as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        result.update(http_status=status, raw_response=raw.name, raw_sha256=hashlib.sha256(body).hexdigest(),
                      response_bytes=len(body), content_type=next((value for key,value in headers.items() if key.lower()=="content-type"), None))
        if status in (401, 403):
            result["status"] = "access_required"
        elif status != 200:
            result["status"] = "unavailable"
        elif len(body) > MAX_BYTES:
            result["status"] = "partial"
            result["warnings"].append("Response byte budget exceeded; no rows parsed.")
        else:
            rows, metadata = parse_votable(body, CONFIG[archive]["columns"])
            records = normalise(archive, rows, target)
            partial = metadata["overflow"] or len(records) > limit
            result.update(records=records[:limit], returned_count=len(records), retained_count=min(len(records),limit),
                response_parsed=True, tap_response=metadata, status="partial" if partial else ("matches" if records else "no_matches"), query_complete=not partial)
            if partial:
                result["warnings"].append("MAXREC/output budget reached or OVERFLOW reported; additional spectra may exist.")
            if any(not record["within_requested_cone"] for record in records):
                result["warnings"].append("Archive returned out-of-cone rows; their positions and candidate flags remain visible.")
            if any(record["product_url"] is None for record in records):
                result["warnings"].append("Some reductions lack a verified native URL pattern; metadata is retained without a guessed link.")
    except (OSError, ValueError, TypeError) as error:
        result.update(status="unavailable", error=f"{type(error).__name__}: {error}")
    result["finished_at_utc"] = utcnow()
    result["receipt"] = attempt + ".receipt.json"
    with (out / result["receipt"]).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    return result
