"""Bounded public optical metadata: documented KOA cones and explicit GTC block.

Only raw Keck observation metadata is returned. A FITS detector frame is not an
extracted spectrum. No product bytes, credentials, retries or guessed URLs.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode

from checkpoint_io import MAX_BYTES, fetch, new_attempt_stem, utcnow
from tap_metadata import parse_votable


KOA_ENDPOINT = "https://koa.ipac.caltech.edu/TAP/sync"
SUPPORTED_BRANCHES = ("lris", "deimos", "esi")
DEFAULT_BRANCHES = SUPPORTED_BRANCHES
LIVE_POSITIVE_BRANCHES = ("lris",)
LIVE_POSITIVE_EVIDENCE = None
KNOWN_RAW_TABLES = ("koa_guider", "koa_kcwi", "koa_deimos", "koa_esi",
    "koa_hires", "koa_lws", "koa_mosfire", "koa_nirc2", "koa_nirc",
    "koa_nires", "koa_nirspec", "koa_osiris", "koa_lris", "koa_kpf")
REFERENCES = (
    "https://koa.ipac.caltech.edu/UserGuide/PyKOA/TAPClients.html",
    "https://koa.ipac.caltech.edu/UserGuide/PyKOA/notebooks/PyVO_LRIS_Synchronous.html",
    "https://gtc.sdc.cab.inta-csic.es/gtc/help/overview.jsp")
# Units explicitly present in the saved TAP_SCHEMA column response. Unknown
# units are not inferred from field names or values.
SCHEMA_UNITS = {
    "lris": {"waveblue": "angstroms", "wavered": "angstroms"},
    "deimos": {"waveblue": "Angstroms", "wavered": "Angstroms",
               "exptime": "sec", "elaptime": "sec"},
    "esi": {"waveblue": "Angstroms", "wavered": "Angstroms",
            "elaptime": "seconds"}}


def _number(value, label, required=False):
    if value in (None, ""):
        if required:
            raise ValueError("Missing " + label + ".")
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean " + label + " is invalid.")
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid " + label + ".") from error
    if not math.isfinite(value):
        raise ValueError("Non-finite " + label + ".")
    return value


def _target(target):
    if not isinstance(target, dict):
        raise ValueError("Target must be a position dictionary.")
    position = dict(target)
    ra, dec, radius = (_number(target.get(key), key, True)
        for key in ("ra_deg", "dec_deg", "radius_arcsec"))
    if not 0 <= ra < 360 or not -90 <= dec <= 90 or not 0 < radius <= 3600:
        raise ValueError("Require RA [0,360), Dec [-90,90], radius (0,3600] arcsec.")
    if target.get("frame", "ICRS") != "ICRS":
        raise ValueError("Only ICRS input coordinates are supported.")
    position.update(ra_deg=ra, dec_deg=dec, radius_arcsec=radius, frame="ICRS",
        position_epoch_jyear=_number(target.get("position_epoch_jyear"), "position epoch"),
        proper_motion_applied=False)
    # Keep supplied identifiers and motion context; neither is used to accept
    # an association or to propagate the search position.
    return position


def _limit(limit):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("Record limit must be an integer from 1 to 10000.")
    return limit


def query_url(target, branch, limit=200):
    position = _target(target)
    _limit(limit)
    if branch not in SUPPORTED_BRANCHES:
        raise ValueError("Unverified Keck branch; use SUPPORTED_BRANCHES.")
    query = ("SELECT * FROM koa_" + branch + " WHERE CONTAINS(POINT('ICRS', ra, dec),"
        f" CIRCLE('ICRS', {position['ra_deg']:.15g}, {position['dec_deg']:.15g},"
        f" {position['radius_arcsec'] / 3600:.15g})) = 1")
    parameters = dict(REQUEST="doQuery", LANG="ADQL", FORMAT="votable",
                      MAXREC=limit + 1, QUERY=query)
    return KOA_ENDPOINT + "?" + urlencode(parameters), parameters


def _lower_keys(row):
    if not isinstance(row, dict) or any(not isinstance(key, str) for key in row):
        raise ValueError("Expected metadata row dictionaries with text fields.")
    names = [name.lower() for name in row]
    if len(set(names)) != len(names):
        raise ValueError("Case-colliding KOA fields.")
    return dict(zip(names, row.values()))


def parse_response(body):
    rows, metadata = parse_votable(body)
    names = [field["name"].lower() for field in metadata["fields"]]
    if len(set(names)) != len(names):
        raise ValueError("Case-colliding KOA fields.")
    if not {"koaid", "filehand", "ra", "dec"}.issubset(names):
        raise ValueError("KOA response lacks raw-observation identity/position columns.")
    return [_lower_keys(row) for row in rows], metadata


def _text(value, label, required=False):
    if value in (None, ""):
        if required:
            raise ValueError("Missing " + label + ".")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Identifier must be exact text or integer: " + label)
    value = str(value)
    if not value or value != value.strip():
        raise ValueError("Invalid " + label + ".")
    return value


def _separation(ra1, dec1, ra2, dec2):
    a, b, c, d = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d-b)/2)**2 + math.cos(b)*math.cos(d)*math.sin((c-a)/2)**2
    return math.degrees(2*math.asin(math.sqrt(min(1, max(0, h))))) * 3600


def normalise_rows(rows, target, branch, fields=()):
    position = _target(target)
    if branch not in SUPPORTED_BRANCHES:
        raise ValueError("Unverified Keck branch.")
    if not isinstance(rows, list):
        raise ValueError("Expected metadata row dictionaries.")
    units = dict(SCHEMA_UNITS[branch])
    units.update({field["name"].lower(): field["unit"] for field in fields
                  if field.get("unit")})
    records, seen = [], set()
    for original in rows:
        row = _lower_keys(original)
        koaid = _text(row.get("koaid"), "KOAID", True)
        filehand = _text(row.get("filehand"), "filehand", True)
        if not filehand.startswith("/") or "://" in filehand:
            raise ValueError("Expected a native KOA file path, not an access URL.")
        if koaid in seen:
            raise ValueError("Duplicate KOAID in one raw-instrument response.")
        seen.add(koaid)
        ra = _number(row.get("ra"), "archive RA", True)
        dec = _number(row.get("dec"), "archive Dec", True)
        if not 0 <= ra < 360 or not -90 <= dec <= 90:
            raise ValueError("Archive coordinates are outside their valid range.")
        instrume = _text(row.get("instrume"), "instrument")
        if instrume and instrume.lower() != branch:
            raise ValueError("Instrument differs from its queried KOA table.")
        separation = _separation(position["ra_deg"], position["dec_deg"], ra, dec)
        exposure = None
        for field in ("exptime", "elaptime"):
            if units.get(field, "").lower() in ("s", "sec", "second", "seconds"):
                exposure = _number(row.get(field), field)
                if exposure is not None:
                    if exposure < 0:
                        raise ValueError("Negative exposure time.")
                    break
        wavelengths = {}
        for field in ("waveblue", "wavered"):
            value = _number(row.get(field), field)
            wavelengths[field] = value * 1e-10 if value is not None and value > 0 and units.get(field, "").lower() in ("angstrom", "angstroms") else None
        if (wavelengths["waveblue"] is not None and wavelengths["wavered"] is not None
                and wavelengths["waveblue"] > wavelengths["wavered"]):
            wavelengths = dict(waveblue=None, wavered=None)
        records.append(dict(archive="keck", record_id="keck:" + branch + ":" + koaid,
            record_kind="observation_product", observation_id=koaid,
            parent_observation_id=None, product_id=filehand,
            product_relation="raw_observation_file", source_detection_verified=False,
            target_name=row.get("targname") or row.get("object"), facility="Keck",
            instrument=instrume or branch.upper(), mode=row.get("obsmode"),
            product_type="raw_frame", calibration_level=0,
            observation_type=row.get("koaimtyp"), filter_name=row.get("filter"),
            data_release="KOA public " + branch.upper() + " raw instrument table",
            release_branch="koa_" + branch, observed_at=row.get("utdatetime") or row.get("date_obs"),
            observation_mjd=_number(row.get("mjd"), "MJD"), exposure_seconds=exposure,
            release_date=None, proprietary_period_native=row.get("propint"),
            has_been_observed=True, access_status="public_metadata",
            ra_deg=ra, dec_deg=dec, separation_arcsec=separation,
            within_requested_cone=separation <= position["radius_arcsec"] + 1e-6,
            archive_position_frame="J2000 equinox as declared by KOA schema",
            archive_position_epoch_jyear=None, coordinate_transform_applied=False,
            em_min_m=wavelengths["waveblue"], em_max_m=wavelengths["wavered"],
            product_url=None, native_product_path=filehand,
            product_access_verified=False, retrieval_method="KOA/PyKOA native filehand",
            reduced_products_enumerated=False, match_method="archive_pointing_cone",
            association_status="candidate_not_verified", footprint_verified=False,
            query_position=position, native_metadata=dict(original)))
    return records


def _save_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def search_optical(archive, target, out, timeout=20, limit=200, fetcher=fetch, *, branches=DEFAULT_BRANCHES):
    """Search up to three verified raw optical instrument tables, or report GTC.

    Successful KOA replies remain partial at facility level: other instruments,
    reduced products and footprint coverage are unresolved. Per-branch statuses
    report matches/no_matches only for each successfully parsed raw cone.
    A caller owns the output-directory writer lock; receipts are append-only.
    """
    if archive not in ("gtc", "keck"):
        raise ValueError("Optical adapter supports gtc or keck.")
    position = _target(target)
    _limit(limit)
    timeout = _number(timeout, "timeout", True)
    if not 0 < timeout <= 120:
        raise ValueError("Timeout must be > 0 and <= 120 seconds.")
    branches = tuple(branches)
    if not branches or len(branches) > 3 or len(set(branches)) != len(branches) or any(branch not in SUPPORTED_BRANCHES for branch in branches):
        raise ValueError("Supply one to three distinct supported optical branches.")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    attempt = new_attempt_stem(out, archive + "-optical")
    result = dict(archive=archive, started_at_utc=utcnow(), query_url=None,
        status="unsupported" if archive == "gtc" else "unavailable", records=[],
        route_state="blocked" if archive == "gtc" else "implemented",
        query_complete=False, selected_queries_complete=False, facility_complete=False,
        target=position, record_limit=limit, response_parsed=False, branches=[],
        returned_count=0, retained_count=0, pagination_supported=False,
        product_downloads=0, receipt=attempt + ".receipt.json", references=list(REFERENCES),
        warnings=["Pointing matches are candidates; source detection, observation-epoch propagation and detector/slit containment are unverified.",
                  "Input epoch and supplied proper-motion context are retained; no motion propagation or frame transformation is applied."])
    if archive == "gtc":
        result.update(coverage="GTC metadata remains indeterminate; no query performed.",
            coverage_status="indeterminate_no_verified_general_route", request_count=0)
        result["warnings"].append("The general coordinate form has preserved HTTP 500 failures. Registered image SIA routes lacked a positive control and do not cover raw observations or all spectra. No absence is inferred.")
    else:
        result.update(coverage="Selected KOA raw optical instrument-table pointing cones.",
            coverage_status="partial_raw_only", requested_tables=["koa_" + branch for branch in branches],
            unqueried_raw_tables=[table for table in KNOWN_RAW_TABLES if table not in {"koa_" + branch for branch in branches}],
            reduced_table="koa_reduced_data", reduced_metadata_status="unvalidated_relationship_query",
            native_product_links_available=False, request_count=0,
            live_positive_validated_branches=list(LIVE_POSITIVE_BRANCHES),
            live_positive_evidence=LIVE_POSITIVE_EVIDENCE,
            live_empty_control_validated=False)
        result["warnings"].extend([
            "Keck coverage is incomplete: other raw tables, quick-look reductions, newer reduced products and contributed datasets are not enumerated.",
            "Raw detector frames do not establish extracted spectra or photometric measurements. Native filehand paths are not public download URLs.",
            "TAP supplies public metadata only. Protected holdings and product access are not checked.",
            "The basic LRIS cone has preserved live positive evidence. Other selected branches have no fresh live positive control. The empty LRIS control returned OVERFLOW without TABLEDATA and does not establish absence; reduced-product discovery remains unresolved."])
        all_records = []
        for branch in branches:
            url, parameters = query_url(position, branch, limit)
            if result["query_url"] is None:
                result["query_url"] = url
            entry = dict(branch_id="koa_" + branch, query_url=url, query_parameters=parameters,
                started_at_utc=utcnow(), status="unavailable", query_complete=False,
                response_parsed=False, returned_count=0, warnings=[])
            try:
                result["request_count"] += 1
                http_status, headers, body = fetcher(url, timeout)
                if not isinstance(body, bytes):
                    raise ValueError("Fetcher must return a byte response body.")
                raw = out / (attempt + "-" + branch + ".response")
                with raw.open("xb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                headers = {str(key).lower(): value for key, value in headers.items()}
                entry.update(http_status=http_status, content_type=headers.get("content-type"),
                    raw_response=raw.name, raw_sha256=hashlib.sha256(body).hexdigest(), response_bytes=len(body))
                if http_status in (401, 403):
                    entry["status"] = "access_required"
                elif http_status != 200:
                    entry["status"] = "unavailable"
                elif len(body) > MAX_BYTES:
                    entry["status"] = "partial"
                    entry["warnings"].append("Response exceeds byte budget; no metadata parsed.")
                else:
                    rows, metadata = parse_response(body)
                    records = normalise_rows(rows, position, branch, metadata["fields"])
                    partial = metadata["overflow"] or len(rows) >= limit + 1
                    entry.update(status="partial" if partial else "matches" if records else "no_matches",
                        query_complete=not partial, response_parsed=True,
                        response_metadata=metadata, returned_count=len(records))
                    if partial:
                        entry["warnings"].append("MAXREC reached or QUERY_STATUS OVERFLOW; additional rows may exist.")
                    if any(not row["within_requested_cone"] for row in records):
                        entry["query_complete"] = False
                        entry["status"] = "partial"
                        entry["warnings"].append("Response includes positions outside the requested cone; all received candidates remain visible.")
                    all_records.extend(records)
            except (OSError, ValueError, TypeError) as error:
                entry.update(status="unavailable", error=type(error).__name__ + ": " + str(error))
            entry["finished_at_utc"] = utcnow()
            result["branches"].append(entry)
            result["warnings"].extend(entry["branch_id"] + ": " + warning for warning in entry["warnings"])
        result.update(records=all_records[:limit], returned_count=len(all_records),
            retained_count=min(limit, len(all_records)),
            response_parsed=any(entry["response_parsed"] for entry in result["branches"]),
            selected_queries_complete=len(all_records) <= limit and all(entry["query_complete"] for entry in result["branches"]))
        statuses = {entry["status"] for entry in result["branches"]}
        if result["response_parsed"] or "partial" in statuses:
            result["status"] = "partial"
        elif statuses == {"access_required"}:
            result["status"] = "access_required"
        if len(all_records) > limit:
            result["warnings"].append("Combined output exceeds record limit; every received row remains in the raw responses.")
    result["finished_at_utc"] = utcnow()
    _save_json(out / result["receipt"], result)
    return result
