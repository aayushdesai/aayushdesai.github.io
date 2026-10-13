#!/usr/bin/env python3
"""Checkpointed public metadata discovery. Prototype: MAST, Gemini, ESO and SDSS-V.

No science-file downloads, credentials or proper-motion fitting.
Only the Python standard library is required on macOS and Linux.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from tap_metadata import parse_votable
from checkpoint_io import new_attempt_stem, saved_directory, verified_bytes
from mast_paging import (collect_pages, mast_url, paging_request,
                         validate_options, verify_result)
from inventory_export import write_inventory

ARCHIVES = ("mast", "gtc", "gemini", "eso", "keck", "desi", "sdss", "sdssspectra", "lamost", "xmm", "chandra")
IMPLEMENTED = {"mast", "gemini", "eso", "sdss", "sdssspectra", "lamost", "desi", "keck", "xmm", "chandra"}
MAX_BYTES = 4 * 1024 * 1024
VERSION = "0.7.0"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_target(ra, dec, radius, epoch=None):
    if not all(math.isfinite(x) for x in (ra, dec, radius)):
        raise ValueError("Coordinates and radius must be finite.")
    if not 0 <= ra < 360 or not -90 <= dec <= 90:
        raise ValueError("Require 0 <= RA < 360 and -90 <= Dec <= 90 degrees.")
    if not 0 < radius <= 3600:
        raise ValueError("Prototype radius must be > 0 and <= 3600 arcsec.")
    if epoch is not None and not math.isfinite(epoch):
        raise ValueError("Coordinate epoch must be finite when supplied.")
    return dict(ra_deg=ra, dec_deg=dec, radius_arcsec=radius,
                frame="ICRS", position_epoch_jyear=epoch,
                proper_motion_applied=False)


def angular_separation(ra1, dec1, ra2, dec2):
    a, b, c, d = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d-b)/2)**2 + math.cos(b)*math.cos(d)*math.sin((c-a)/2)**2
    return math.degrees(2 * math.asin(math.sqrt(min(1, max(0, h))))) * 3600


def query_url(archive, target, limit=500):
    ra, dec, radius = (target[k] for k in ("ra_deg", "dec_deg", "radius_arcsec"))
    if archive == "mast":
        request = dict(service="Mast.Caom.Cone",
                       params=dict(ra=ra, dec=dec, radius=radius/3600),
                       format="json", pagesize=limit+1, page=1,
                       removenullcolumns=False)
        return "https://mast.stsci.edu/api/v0/invoke?" + urlencode(
            dict(request=json.dumps(request, separators=(',', ':'))))
    if archive == "gemini":
        return ("https://archive.gemini.edu/jsonsummary/canonical/"
                f"ra={ra:.12g}/dec={dec:.12g}/sr={radius:.12g}")
    if archive == "sdss":
        return "https://api.sdss.org/valis/query/cone?" + urlencode(
            dict(ra=ra, dec=dec, radius=radius / 3600,
                 units="degree", release="DR19"))
    if archive == "eso":
        query = (f"SELECT TOP {limit + 1} obs_publisher_did, obs_id, target_name, "
                 "instrument_name, s_ra, s_dec, t_min, t_exptime, "
                 "dataproduct_type, calib_level, obs_collection, access_url, "
                 "access_format, s_region FROM ivoa.ObsCore WHERE "
                 "CONTAINS(POINT('ICRS',s_ra,s_dec),"
                 f"CIRCLE('ICRS',{ra:.12g},{dec:.12g},{radius/3600:.12g}))=1")
        return "https://archive.eso.org/tap_obs/sync?" + urlencode(
            dict(REQUEST="doQuery", LANG="ADQL", FORMAT="votable",
                 MAXREC=limit + 1, QUERY=query))
    return None


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalise(archive, rows, target):
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Expected a JSON list of metadata records.")
    records = []
    for row in rows:
        if archive == "gemini":
            identifier = row.get("name") or row.get("filename")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Gemini record lacks a file identifier.")
            record = dict(
                record_id=identifier, record_kind="observation_product",
                observation_id=row.get("observation_id"), target_name=row.get("object"),
                instrument=row.get("instrument"), ra_deg=number(row.get("ra")),
                dec_deg=number(row.get("dec")), observed_at=row.get("ut_datetime"),
                exposure_seconds=number(row.get("exposure_time")),
                release_date=row.get("release"), has_been_observed=None,
                product_url="https://archive.gemini.edu/file/" + quote(identifier, safe=""),
                match_method="archive_pointing_cone", data_release=None,
                filter_name=row.get("filter_name"), reduction=row.get("reduction"),
                engineering=row.get("engineering"),
                calibration_program=row.get("calibration_program"),
                observation_class=row.get("observation_class"),
                observation_type=row.get("observation_type"),
                qa_state=row.get("qa_state"), mode=row.get("mode"))
        elif archive == "mast":
            identifier = row.get("obsid")
            if not isinstance(identifier, (int, str)) or isinstance(identifier, bool) or identifier == "":
                raise ValueError("MAST record lacks obsid.")
            uri = row.get("dataURL")
            url = ("https://mast.stsci.edu/api/v0.1/Download/file?" + urlencode(dict(uri=uri))
                   if isinstance(uri, str) and uri.startswith('mast:') else uri)
            record = dict(record_id=str(identifier), record_kind="observation",
                observation_id=row.get("obs_id"), product_query_obsid=str(identifier),
                target_name=row.get("target_name"), instrument=row.get("instrument_name"),
                ra_deg=number(row.get("s_ra")), dec_deg=number(row.get("s_dec")),
                observed_at=None, observation_mjd=number(row.get("t_min")),
                exposure_seconds=number(row.get("t_exptime")),
                release_mjd=number(row.get("t_obs_release")), release_date=None,
                has_been_observed=None, product_url=url, product_uri=uri,
                product_type=row.get("dataproduct_type"),
                collection=row.get("obs_collection"), access_status=row.get("dataRights"),
                filter_name=row.get("filters"), footprint_stcs=row.get("s_region"),
                footprint_verified=False, match_method="archive_caom_cone",
                data_release=None)
        elif archive == "eso":
            identifier = row.get("obs_publisher_did")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("ESO record lacks obs_publisher_did.")
            record = dict(
                record_id=identifier, record_kind="observation_product",
                observation_id=row.get("obs_id"), target_name=row.get("target_name"),
                instrument=row.get("instrument_name"), ra_deg=number(row.get("s_ra")),
                dec_deg=number(row.get("s_dec")), observed_at=None,
                observation_mjd=number(row.get("t_min")),
                exposure_seconds=number(row.get("t_exptime")), release_date=None,
                has_been_observed=None, product_url=row.get("access_url"),
                product_access_format=row.get("access_format"),
                product_type=row.get("dataproduct_type"),
                calibration_level=number(row.get("calib_level")),
                collection=row.get("obs_collection"), footprint_stcs=row.get("s_region"),
                footprint_verified=False, match_method="archive_position_cone",
                data_release=None)
        elif archive == "sdss":
            identifier = row.get("sdss_id")
            if not isinstance(identifier, (str, int)) or isinstance(identifier, bool):
                raise ValueError("SDSS record lacks sdss_id.")
            observed = row.get("has_been_observed")
            if observed is not None and not isinstance(observed, bool):
                raise ValueError("SDSS has_been_observed must be a boolean or null.")
            record = dict(
                record_id=str(identifier), record_kind="catalogue_target",
                observation_id=None, target_name=None, instrument=None,
                ra_deg=number(row.get("ra_sdss_id")), dec_deg=number(row.get("dec_sdss_id")),
                observed_at=None, observation_mjd=number(row.get("mjd")),
                exposure_seconds=None, release_date=None,
                has_been_observed=observed, product_url=None,
                match_method="catalogue_position_cone", data_release="DR19",
                source_release_label=row.get("release"))
        else:
            raise ValueError("No normaliser for archive: " + archive)
        record["archive"] = archive
        record["separation_arcsec"] = None
        record["within_requested_cone"] = None
        ra, dec = record["ra_deg"], record["dec_deg"]
        if ra is not None and dec is not None and 0 <= ra < 360 and -90 <= dec <= 90:
            separation = angular_separation(target["ra_deg"], target["dec_deg"], ra, dec)
            record["separation_arcsec"] = separation
            record["within_requested_cone"] = separation <= target["radius_arcsec"] + 1e-6
        record["association_status"] = "candidate_not_verified"
        records.append(record)
    return records


def parse_mast(body):
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get('status') not in ('COMPLETE', 'EXECUTING'):
        raise ValueError("MAST query failed or returned an unknown status.")
    rows = payload.get('data')
    if rows is None and payload['status'] == 'EXECUTING':
        rows = []
    if not isinstance(rows, list):
        raise ValueError("MAST response lacks a data array.")
    paging = payload.get('paging') or {}
    if not isinstance(paging, dict):
        raise ValueError("Invalid MAST paging metadata.")
    pages = number(paging.get('pagesFiltered'))
    total = number(paging.get('rowsFiltered'))
    incomplete = (payload['status'] == 'EXECUTING' or
                  (pages is not None and pages > 1) or
                  (total is not None and total > len(rows)))
    return rows, dict(status=payload['status'], paging=paging,
                      fields=payload.get('fields'), incomplete=incomplete)


def fetch(url, timeout):
    request = Request(url, headers={"Accept": "application/json, application/x-votable+xml, text/xml",
                                   "User-Agent": "MusingsArchiveDiscovery/" + VERSION})
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BYTES + 1)
        return response.status, dict(response.headers), body


def search_archive(archive, target, out, timeout, limit, fetcher=fetch,
                   page_size=500, retries=2, sleeper=time.sleep):
    out = Path(out)
    if archive in ("lamost", "desi", "sdssspectra", "xmm", "chandra", "keck", "gtc"):
        if archive == "lamost":
            from lamost_metadata import search_lamost
            result = search_lamost(target, out, timeout, limit, fetcher)
        elif archive in ("desi", "sdssspectra"):
            from spectral_routes import search_spectra
            result = search_spectra(archive, target, out, timeout, limit, fetcher)
        elif archive in ("xmm", "chandra"):
            from xray_metadata import search_xray
            result = search_xray(archive, target, out, timeout, limit, fetcher)
        else:
            from optical_metadata import search_optical
            result = search_optical(archive, target, out, timeout, limit, fetcher)
        if result.get("receipt"):
            result["receipt_sha256"] = hashlib.sha256((out / result["receipt"]).read_bytes()).hexdigest()
        return result
    url = query_url(archive, target, limit)
    result = dict(archive=archive, started_at_utc=utcnow(), query_url=url,
                  status="unsupported", records=[], warnings=[], query_complete=False)
    if url is None:
        result["warnings"] = ["Adapter is not implemented in this prototype; no query was sent."]
        result["finished_at_utc"] = utcnow()
        return result
    result["coverage"] = {
        "mast": "MAST CAOM observation cone with bounded pagination; associated products not enumerated",
        "gemini": "Gemini canonical file metadata; pointing centres only",
        "sdss": "SDSS-V DR19 Valis targets; no exposure or product enumeration",
        "eso": "ESO ivoa.ObsCore reduced-product metadata; position cone only; raw dbo.raw not searched"
    }[archive]
    result["warnings"] = ["No proper-motion propagation or observation-footprint verification.",
                           "Matches are candidates; archive-wide completeness is not established."]
    attempt = new_attempt_stem(out, archive)
    if archive == "mast":
        params = dict(ra=target["ra_deg"], dec=target["dec_deg"],
                      radius=target["radius_arcsec"] / 3600)
        paging = collect_pages("Mast.Caom.Cone", params, out / "mast-pages",
            limit, page_size, timeout, retries, fetcher, sleeper,
            validator=lambda rows: normalise("mast", rows, target), identity_field="obsid")
        result.update(paging)
        result["records"] = normalise("mast", paging["records"], target)
        result["paging_directory"] = "mast-pages"
        result["query_url"] = mast_url("Mast.Caom.Cone", params, 1, min(page_size, limit + 1))
        result["response_parsed"] = bool(paging["page_receipts"] or paging["records"])
        result["warnings"] = ["No proper-motion propagation or observation-footprint verification.",
            "Matches are candidates; query completion does not establish archive-wide completeness."] + paging["warnings"]
        atomic_json(out / (attempt + ".receipt.json"), result)
        return result
    raw_path = out / (attempt + ".response")
    try:
        status, headers, body = fetcher(url, timeout)
        with raw_path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        result.update(http_status=status, content_type=headers.get("Content-Type"),
                      raw_response=raw_path.name, raw_sha256=hashlib.sha256(body).hexdigest(),
                      response_bytes=len(body))
        if status in (401, 403):
            result["status"] = "access_required"
        elif status != 200:
            result["status"] = "unavailable"
        elif len(body) > MAX_BYTES:
            result["status"] = "partial"
            result["warnings"].append("Response exceeded the byte budget; preserved prefix only, no records parsed.")
        else:
            if archive == "eso":
                rows, tap = parse_votable(body, ("obs_publisher_did", "s_ra", "s_dec"))
                result["tap_response"] = tap
            elif archive == "mast":
                rows, mast = parse_mast(body)
                result["mast_response"] = mast
            else:
                rows = json.loads(body)
            records = normalise(archive, rows, target)
            result.update(returned_count=len(records), retained_count=min(limit, len(records)),
                          records=records[:limit], response_parsed=True)
            result["status"] = "partial" if len(records) > limit else ("matches" if records else "no_matches")
            if len(records) > limit:
                result["warnings"].append("Normalised output reached the row budget; full response is retained.")
            if archive == "gemini" and len(records) == 500:
                result["status"] = "partial"
                result["possibly_truncated"] = True
                result["warnings"].append(
                    "Exactly 500 rows returned: possible service cap. Pagination has not been verified.")
            if result.get("tap_response", {}).get("overflow"):
                result["status"] = "partial"
                result["warnings"].append("TAP service reported OVERFLOW; result is truncated.")
            if result.get("mast_response", {}).get("incomplete"):
                result["status"] = "partial"
                result["warnings"].append("MAST is still executing or has additional pages; polling/pagination remains incomplete.")
    except (OSError, URLError, ValueError, TypeError) as error:
        result["status"] = "unavailable"
        result["error"] = f"{type(error).__name__}: {error}"
    result["finished_at_utc"] = utcnow()
    atomic_json(out / (attempt + ".receipt.json"), result)
    return result


def run(target, archives, out, resume=False, retry_failed=False, timeout=30, limit=500, fetcher=fetch,
        page_size=500, retries=2, sleeper=time.sleep):
    validate_options(limit, page_size, timeout, retries)
    archives = list(archives)
    if not archives or len(archives) != len(set(archives)):
        raise ValueError("Supply a nonempty list of distinct archives.")
    if retry_failed and not resume:
        raise ValueError("retry-failed requires resume.")
    if any(a not in ARCHIVES for a in archives):
        raise ValueError("Unknown archive.")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    request = dict(schema_version=1, tool_version=VERSION, target=target,
                   archives=list(archives), timeout_seconds=timeout, record_limit=limit,
                   response_byte_limit=MAX_BYTES, mast_page_size=page_size,
                   mast_retries=retries)
    with (out / ".writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_path = out / "request.json"
        if request_path.exists():
            if not resume:
                raise ValueError("Output already has a request; use --resume or a new directory.")
            if json.loads(request_path.read_text()) != request:
                raise ValueError("Resume request differs from the checkpoint; use a new directory.")
        else:
            if resume:
                raise ValueError("No request checkpoint exists to resume.")
            if any(p.name != ".writer.lock" for p in out.iterdir()):
                raise ValueError("New output directory must be empty.")
            atomic_json(request_path, request)
        report_path = out / "report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else dict(
            request=request, created_at_utc=utcnow(), archives={})
        for archive in archives:
            cached = report["archives"].get(archive)
            if cached:
                if cached.get("raw_response"):
                    verified_bytes(out, cached["raw_response"], cached["raw_sha256"])
                if cached.get("paging_directory"):
                    params = dict(ra=target["ra_deg"], dec=target["dec_deg"],
                                  radius=target["radius_arcsec"] / 3600)
                    expected = paging_request("Mast.Caom.Cone", params, limit, page_size, timeout, retries, "obsid")
                    verify_result(saved_directory(out, cached["paging_directory"]), cached, expected)
                if cached.get("receipt"):
                    verified_bytes(out, cached["receipt"], cached["receipt_sha256"])
                for branch in cached.get("branches", []):
                    if branch.get("raw_response"):
                        verified_bytes(out, branch["raw_response"], branch["raw_sha256"])
            if cached and not (retry_failed and cached["status"] in ("unavailable", "access_required", "partial")):
                continue
            report["archives"][archive] = search_archive(archive, target, out, timeout, limit,
                fetcher, page_size, retries, sleeper)
            report["updated_at_utc"] = utcnow()
            atomic_json(report_path, report)
        write_inventory(report, out, report_path)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ra", type=float, help="ICRS right ascension in degrees")
    source.add_argument("--name", help="Resolve an unambiguous name through MAST")
    parser.add_argument("--dec", type=float, help="ICRS declination in degrees; required with --ra")
    parser.add_argument("--radius-arcsec", type=float, default=5)
    parser.add_argument("--epoch", type=float, help="Position epoch, recorded without propagating motion")
    parser.add_argument("--archives", nargs="+", choices=ARCHIVES, default=list(ARCHIVES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--record-limit", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=500, help="MAST rows per page (1 to 1000)")
    parser.add_argument("--retries", type=int, default=2, help="Retries per MAST page (0 to 3)")
    args = parser.parse_args()
    try:
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120 or args.record_limit < 1:
            raise ValueError("Require 0 < timeout <= 120 and record-limit >= 1.")
        if args.retry_failed and not args.resume:
            raise ValueError("--retry-failed requires --resume.")
        if args.name is not None:
            if args.dec is not None:
                raise ValueError("--dec cannot be combined with --name.")
            validate_target(0, 0, args.radius_arcsec, args.epoch)
            from resolve_source import resolve_name
            resolution = resolve_name(args.name, args.output / 'resolution', args.resume,
                                      args.timeout, retry_failed=args.retry_failed)
            if resolution['status'] != 'resolved':
                raise ValueError(f"Name resolution is {resolution['status']}; inspect {args.output / 'resolution/report.json'} and supply coordinates if needed.")
            candidate = resolution['candidates'][0]
            target = validate_target(float(candidate['ra']), float(candidate['decl']), args.radius_arcsec, args.epoch)
            target.update(input_name=args.name, resolver=candidate.get('resolver'),
                          canonical_name=candidate.get('canonicalName'),
                          resolution_sha256=resolution['raw_sha256'])
            args.output = args.output / 'search'
        else:
            if args.dec is None:
                raise ValueError("--dec is required with --ra.")
            target = validate_target(args.ra, args.dec, args.radius_arcsec, args.epoch)
        report = run(target, list(dict.fromkeys(args.archives)), args.output,
                     args.resume, args.retry_failed, args.timeout, args.record_limit,
                     page_size=args.page_size, retries=args.retries)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    for archive, result in report["archives"].items():
        print(f"{archive}: {result['status']} ({len(result['records'])} retained records)")
    print(f"Checkpoint: {args.output.resolve() / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
