"""Bounded LAMOST SSA spectrum metadata, with immutable local responses.

No spectrum bytes are downloaded. Each MRS row can describe one component
of a parent FITS bundle; component rows are never merged by observation ID.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from checkpoint_io import MAX_BYTES, fetch, new_attempt_stem, utcnow
from tap_metadata import parse_votable


DEFAULT_BRANCHES = (("dr12", "v2.0", "lrs"), ("dr12", "v2.0", "mrs"))
SUPPORTED_BRANCHES = tuple((release, version, resolution)
    for release, version in (("dr12", "v2.0"), ("dr12", "v1.1"), ("dr11", "v2.0"))
    for resolution in ("lrs", "mrs"))
IDENTIFIER_FIELDS = ("obsid", "gp_id", "gaia_source_id", "ps_id", "fiberid",
                     "spid", "lmjd", "mjd", "lmjm", "ssa_dstitle")
BASE = "https://www.lamost.org/openapi"


def _number(value, name, required=False):
    if value is None or value == "":
        if required:
            raise ValueError("Missing " + name + ".")
        return None
    if isinstance(value, bool):
        raise ValueError("Invalid boolean " + name + ".")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid " + name + ".") from error
    if not math.isfinite(number):
        raise ValueError("Non-finite " + name + ".")
    return number


def _target(target):
    if not isinstance(target, dict):
        raise ValueError("Target must be a position dictionary.")
    ra = _number(target.get("ra_deg"), "RA", True)
    dec = _number(target.get("dec_deg"), "Dec", True)
    radius = _number(target.get("radius_arcsec"), "radius", True)
    if not 0 <= ra < 360 or not -90 <= dec <= 90 or not 0 < radius <= 3600:
        raise ValueError("Require ICRS RA [0,360), Dec [-90,90], radius (0,3600] arcsec.")
    if target.get("frame", "ICRS") != "ICRS":
        raise ValueError("Only ICRS coordinates are supported.")
    epoch = _number(target.get("position_epoch_jyear"), "position epoch")
    return dict(ra_deg=ra, dec_deg=dec, radius_arcsec=radius, frame="ICRS",
                position_epoch_jyear=epoch, proper_motion_applied=False)


def _branch(branch):
    if not isinstance(branch, (tuple, list)) or tuple(branch) not in SUPPORTED_BRANCHES:
        raise ValueError("Unsupported LAMOST release branch; use SUPPORTED_BRANCHES.")
    release, version, resolution = branch
    return dict(dr_version=release, sub_version=version, resolution=resolution,
                branch_id="-".join(branch), data_release=release.upper() + " " + version,
                endpoint=f"{BASE}/{release}/{version}/{resolution}/voservice/ssap",
                public_metadata_evidence="preserved anonymous positive SSA response",
                release_documentation=f"https://www.lamost.org/{release}/{version}/vo",
                saved_version_inventory_public_status="public" if release == "dr11" else "internal",
                saved_version_inventory_has_mrs=not (release == "dr12" and version == "v2.0"))


def query_url(target, branch, limit=500):
    """SSA SIZE is a diameter in degrees; limit+1 detects the local row budget."""
    position = _target(target)
    descriptor = _branch(branch)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
        raise ValueError("Record limit must be an integer from 1 to 100000.")
    parameters = dict(request="queryData",
        pos=f"{position['ra_deg']:.15g},{position['dec_deg']:.15g}",
        size=2 * position["radius_arcsec"] / 3600, **{"output.fmt": "json"}, maxrec=limit + 1)
    return descriptor["endpoint"] + "?" + urlencode(parameters), parameters


def _json_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("Duplicate JSON field: " + key)
        row[key] = value
    return row


def _reject_constant(value):
    raise ValueError("Non-standard JSON constant: " + value)


def parse_response(body):
    """Accept JSON rows or strict SSA TABLEDATA; application errors are failures."""
    if not isinstance(body, bytes):
        raise ValueError("Response body must be bytes.")
    if body.lstrip().startswith(b"<"):
        rows, metadata = parse_votable(body, ("obsid", "ra_obs", "dec_obs"))
        return rows, dict(metadata, format="votable", service_completion_reported=True)
    rows = json.loads(body, object_pairs_hook=_json_object, parse_constant=_reject_constant)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected a JSON array of spectrum metadata; application error or invalid schema.")
    return rows, dict(format="json", overflow=False, service_completion_reported=False)


def _identifier(value, name, required=False, decimal=False):
    if value is None or value == "":
        if required:
            raise ValueError("Missing " + name + ".")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Identifier must be exact text or integer, never a float: " + name)
    text = str(value)
    if text != text.strip() or not text or (decimal and not text.isdecimal()):
        raise ValueError("Invalid identifier: " + name)
    return text


def _separation(ra1, dec1, ra2, dec2):
    a, b, c, d = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d - b) / 2) ** 2 + math.cos(b) * math.cos(d) * math.sin((c - a) / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(min(1, max(0, h))))) * 3600


def _product_url(row, descriptor, obsid):
    native = row.get("accref")
    if native not in (None, ""):
        if not isinstance(native, str):
            raise ValueError("Invalid SSA access reference.")
        url = urlsplit(native)
        if (url.scheme not in ("http", "https") or url.hostname not in ("lamost.org", "www.lamost.org")
                or url.username or url.password or url.port not in (None, 80, 443)):
            raise ValueError("Unexpected SSA access reference host or scheme.")
        if any(parse_qs(url.query).get("token", [])):
            raise ValueError("Credential-bearing SSA access reference is unsupported.")
        return native, "archive_ssa_access_reference"
    # The saved OpenAPI schema documents this route. DR12 v2.0 native retrieval
    # receipts also validate it; a metadata link still does not test accessibility.
    return (f"{BASE}/{descriptor['dr_version']}/{descriptor['sub_version']}/"
            f"{descriptor['resolution']}/spectrum/fits?" + urlencode(dict(obsid=obsid)),
            "documented_openapi_obsid_route")


def normalise_rows(rows, target, branch):
    position = _target(target)
    descriptor = _branch(branch)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected metadata row dictionaries.")
    records, seen = [], set()
    for original in rows:
        row = dict(original)
        obsid = _identifier(row.get("obsid"), "obsid", True, True)
        for key in IDENTIFIER_FIELDS:
            if key in row:
                row[key] = _identifier(row[key], key)
        if descriptor["resolution"] == "mrs":
            component = _identifier(row.get("mobsid"), "mobsid", True)
        else:
            component = obsid
        key = descriptor["branch_id"] + ":" + component
        if key in seen:
            raise ValueError("Duplicate spectrum component within one release response: " + key)
        seen.add(key)
        ra = _number(row.get("ra_obs"), "ra_obs", True)
        dec = _number(row.get("dec_obs"), "dec_obs", True)
        if not 0 <= ra < 360 or not -90 <= dec <= 90:
            raise ValueError("Spectrum coordinates are outside their valid range.")
        separation = _separation(position["ra_deg"], position["dec_deg"], ra, dec)
        url, route = _product_url(row, descriptor, obsid)
        records.append(dict(archive="lamost", record_id=key,
            record_kind="observation_product", observation_id=obsid,
            parent_observation_id=obsid, product_id=descriptor["branch_id"] + ":" + obsid,
            native_component_id=component if descriptor["resolution"] == "mrs" else None,
            product_relation="component_of_native_spectrum_bundle" if descriptor["resolution"] == "mrs" else "native_spectrum",
            source_detection_verified=False, target_name=row.get("designation"),
            facility="LAMOST", instrument="LAMOST spectrographs", mode=descriptor["resolution"].upper(),
            product_type="spectrum", data_release=descriptor["data_release"],
            release_branch=descriptor["branch_id"], observed_at=row.get("obsdate"),
            observation_mjd=None, archive_night_mjd=_number(row.get("mjd"), "mjd"),
            exposure_seconds=None, release_date=None, has_been_observed=True,
            ra_deg=ra, dec_deg=dec, separation_arcsec=separation,
            within_requested_cone=separation <= position["radius_arcsec"] + 1e-6,
            product_url=url, native_product_url=row.get("accref"), product_url_route=route,
            product_access_verified=False, product_access_format=row.get("mime"),
            native_file=row.get("file"), publisher_dataset_id=row.get("ssa_pubDID"),
            band=row.get("band"), coadd=row.get("coadd"), fibre_type=row.get("fibertype"),
            gaia_source_id=row.get("gaia_source_id"), match_method="archive_spectrum_position_cone",
            association_status="candidate_not_verified", footprint_verified=False,
            query_position=position, native_metadata=row))
    return records


def _save_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def search_lamost(target, out, timeout=30, limit=500, fetcher=fetch, *, branches=DEFAULT_BRANCHES):
    """Return common archive records and per-branch evidence; one request per branch.

    The record limit applies to the combined output. All returned response bytes
    are kept, including rows beyond that limit. No pagination, retry, or native
    download is performed. A caller owns any query-directory writer lock.
    """
    position = _target(target)
    timeout = _number(timeout, "timeout", True)
    if not 0 < timeout <= 120:
        raise ValueError("Timeout must be > 0 and <= 120 seconds.")
    branches = tuple(tuple(branch) for branch in branches)
    if not branches or len(set(branches)) != len(branches):
        raise ValueError("Supply distinct, nonempty LAMOST branches.")
    requests = [(branch, *query_url(position, branch, limit)) for branch in branches]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    attempt = new_attempt_stem(out, "lamost")
    result = dict(archive="lamost", started_at_utc=utcnow(), query_url=requests[0][1],
        query_urls=[request[1] for request in requests], status="unavailable", records=[],
        warnings=["Cone matches are candidates; no source identity, observation-epoch propagation or fibre acceptance is verified.",
                  "Coverage is limited to the named release branches. Photometric catalogue fields are context; light curves, raw exposures and other releases are not enumerated."],
        coverage="LAMOST " + ", ".join(_branch(branch)["branch_id"] for branch in branches) + " SSA spectrum-component metadata",
        query_complete=False, target=position, record_limit=limit,
        pagination_supported=False, branches=[], response_parsed=False,
        returned_count=0, retained_count=0, receipt=attempt + ".receipt.json")
    all_records = []
    for number, (branch, url, parameters) in enumerate(requests, 1):
        entry = dict(_branch(branch), query_url=url, query_parameters=parameters,
            started_at_utc=utcnow(), status="unavailable", query_complete=False,
            response_parsed=False, warnings=[], returned_count=0, records=[])
        raw = out / (attempt + f"-branch-{number}.response")
        try:
            status, headers, body = fetcher(url, timeout)
            if not isinstance(body, bytes):
                raise ValueError("Fetcher must return a byte response body.")
            with raw.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            headers = {str(key).lower(): value for key, value in headers.items()}
            entry.update(http_status=status, content_type=headers.get("content-type"),
                raw_response=raw.name, raw_sha256=hashlib.sha256(body).hexdigest(), response_bytes=len(body))
            if status in (401, 403):
                entry["status"] = "access_required"
            elif status != 200:
                entry["status"] = "unavailable"
            elif len(body) > MAX_BYTES:
                entry["status"] = "partial"
                entry["warnings"].append("Response exceeds the byte budget; no metadata parsed.")
            else:
                rows, metadata = parse_response(body)
                records = normalise_rows(rows, position, branch)
                entry.update(response_parsed=True, response_metadata=metadata,
                             returned_count=len(records), records=records)
                truncated = len(records) >= parameters["maxrec"] or metadata["overflow"]
                entry["status"] = "partial" if truncated else ("matches" if records else "no_matches")
                entry["query_complete"] = metadata["service_completion_reported"] and not truncated
                if truncated:
                    entry["warnings"].append("MAXREC reached or service reported OVERFLOW; additional rows may exist.")
                if not metadata["service_completion_reported"]:
                    entry["warnings"].append("JSON provides no independent completion status; rows below MAXREC do not prove completion.")
                if any(not record["within_requested_cone"] for record in records):
                    entry["warnings"].append("Response includes positions outside the requested cone; those candidate rows remain visible.")
                all_records.extend(records)
        except (OSError, ValueError, TypeError) as error:
            entry.update(status="unavailable", error=f"{type(error).__name__}: {error}")
        entry["finished_at_utc"] = utcnow()
        # Raw bytes retain every row; summary receipts avoid duplicating them.
        entry.pop("records", None)
        result["branches"].append(entry)
        result["warnings"].extend(entry["branch_id"] + ": " + warning for warning in entry["warnings"])
    result.update(records=all_records[:limit], returned_count=len(all_records),
                  retained_count=min(limit, len(all_records)),
                  distinct_parent_product_count=len({row["product_id"] for row in all_records[:limit]}),
                  response_parsed=any(entry["response_parsed"] for entry in result["branches"]))
    statuses = {entry["status"] for entry in result["branches"]}
    failures = statuses & {"unavailable", "access_required", "partial"}
    if len(all_records) > limit or "partial" in statuses or (failures and result["response_parsed"]):
        result["status"] = "partial"
    elif not result["response_parsed"]:
        result["status"] = "access_required" if statuses == {"access_required"} else "unavailable"
    else:
        result["status"] = "matches" if all_records else "no_matches"
    result["query_complete"] = (len(all_records) <= limit and
        all(entry["query_complete"] for entry in result["branches"]))
    if len(all_records) > limit:
        result["warnings"].append("Combined normalised output exceeds the record limit; complete received bytes are retained.")
    result["finished_at_utc"] = utcnow()
    _save_json(out / result["receipt"], result)
    return result
