"""Bounded MAST paging with immutable attempts and resumable accepted pages.

The caller owns the writer lock. Completing a service query does not establish
source detection, footprint containment or coverage of all MAST collections.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from checkpoint_io import MAX_BYTES, atomic_json, fetch, utcnow, verified_bytes, new_attempt_stem


def mast_url(service, params, page, page_size):
    request = dict(service=service, params=params, format="json", page=page,
                   pagesize=page_size, removenullcolumns=False)
    return "https://mast.stsci.edu/api/v0/invoke?" + urlencode(
        dict(request=json.dumps(request, separators=(",", ":"))))


def integer(value):
    """Read integral counts without converting exact identifiers through float."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number >= 0 and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def parse_mast(body):
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("status") not in ("COMPLETE", "EXECUTING"):
        message = payload.get("msg", "") if isinstance(payload, dict) else ""
        raise ValueError("MAST query failed or returned an unknown status: " + str(message)[:500])
    rows = payload.get("data", [] if payload["status"] == "EXECUTING" else None)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("MAST response lacks a data array of records.")
    paging = payload.get("paging") or {}
    if not isinstance(paging, dict):
        raise ValueError("Invalid MAST paging metadata.")
    pages, total = integer(paging.get("pagesFiltered")), integer(paging.get("rowsFiltered"))
    incomplete = (payload["status"] == "EXECUTING" or
                  (pages is not None and pages > 1) or
                  (total is not None and total > len(rows)))
    return rows, dict(status=payload["status"], paging=paging,
                      fields=payload.get("fields"), incomplete=incomplete)


def validate_options(limit, page_size, timeout, retries):
    for name, value, maximum in (("record-limit", limit, 10000),
                                 ("page-size", page_size, 1000)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Require 0 < timeout <= 120.")
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
        raise ValueError("retries must be an integer from 0 to 3.")


def paging_request(service, params, limit, page_size, timeout, retries, identity_field):
    validate_options(limit, page_size, timeout, retries)
    return dict(schema_version=1, service=service, params=params,
                record_limit=limit, page_size=min(page_size, limit + 1),
                timeout=timeout, retries=retries, response_byte_limit=MAX_BYTES,
                identity_field=identity_field)


def load_manifest(out, request=None):
    manifest = json.loads((out / "manifest.json").read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list) or not isinstance(manifest.get("request"), dict):
        raise ValueError("Invalid MAST paging manifest.")
    if request is not None and manifest["request"] != request:
        raise ValueError("Saved MAST paging request differs; use a fresh directory.")
    return manifest


def replay_page(out, saved, url, page):
    receipt = json.loads(verified_bytes(out, saved["receipt"], saved["receipt_sha256"]))
    if receipt.get("query_url") != url or receipt.get("page") != page or receipt.get("http_status") != 200:
        raise ValueError("Saved MAST page does not match its request.")
    body = verified_bytes(out, receipt["raw_response"], receipt["raw_sha256"])
    if len(body) > MAX_BYTES:
        raise ValueError("Saved accepted MAST page exceeds the response budget.")
    rows, meta = parse_mast(body)
    if meta["status"] != "COMPLETE":
        raise ValueError("Saved accepted MAST page is not complete.")
    return rows, meta


def row_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def page_problem(rows, meta, page, size, expected, seen, identity_field, page_hashes=None):
    paging = meta["paging"]
    total, pages, returned_page, returned_size = [integer(paging.get(key)) for key in
        ("rowsFiltered", "pagesFiltered", "page", "pageSize")]
    if None in (total, pages, returned_page, returned_size):
        return "Missing or invalid paging metadata; complete enumeration cannot be verified."
    valid_pages = {math.ceil(total / size)} if total else {0, 1}
    expected_here = min(size, max(0, total - (page - 1) * size))
    if (returned_page != page or returned_size != size or pages not in valid_pages or
            (expected is not None and (total, pages) != expected) or len(rows) != expected_here or
            ("rows" in paging and integer(paging["rows"]) != len(rows))):
        return "Inconsistent or changing paging metadata; enumeration remains incomplete."
    if rows and page_hashes is not None and row_digest(rows) in page_hashes:
        return "Service repeated a previous page; enumeration stopped."
    if identity_field:
        identifiers = [str(row[identity_field]) for row in rows]
        if len(set(identifiers)) != len(identifiers) or any(key in seen for key in identifiers):
            return "Repeated record identifier across service pages; enumeration stopped."
    return None


def collect_pages(service, params, out, limit=500, page_size=500, timeout=30,
                  retries=2, fetcher=fetch, sleeper=time.sleep, validator=None,
                  identity_field=None):
    request = paging_request(service, params, limit, page_size, timeout, retries, identity_field)
    page_size = request["page_size"]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = load_manifest(out, request)
    else:
        if any(out.iterdir()):
            raise ValueError("MAST paging directory lacks its manifest; preserve it and use a fresh directory.")
        manifest = dict(request=request, created_at_utc=utcnow(), pages=[])
        atomic_json(manifest_path, manifest)
    result = dict(records=[], returned_count=0, retained_count=0, query_complete=False,
                  page_receipts=[], warnings=[], status="unavailable", next_page=1)
    expected = None
    all_rows, seen, page_hashes = [], set(), set()
    page = 1
    while True:
        url = mast_url(service, params, page, page_size)
        if page <= len(manifest["pages"]):
            saved = manifest["pages"][page - 1]
            rows, meta = replay_page(out, saved, url, page)
            if validator:
                validator(rows)
            problem = page_problem(rows, meta, page, page_size, expected, seen, identity_field, page_hashes)
            if problem:
                raise ValueError("Saved accepted MAST page is inconsistent: " + problem)
        else:
            accepted = False
            for attempt in range(retries + 1):
                stamp = new_attempt_stem(out, f"page-{page:06d}")
                receipt = dict(page=page, query_url=url, started_at_utc=utcnow())
                retry, delay = False, min(2 ** attempt, 8)
                failure_status = "unavailable"
                try:
                    status, headers, body = fetcher(url, timeout)
                    headers = {key.lower(): value for key, value in headers.items()}
                    raw = out / (stamp + ".response")
                    with raw.open("xb") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    receipt.update(http_status=status, response_headers=headers,
                                   raw_response=raw.name, response_bytes=len(body),
                                   raw_sha256=hashlib.sha256(body).hexdigest())
                    if status != 200:
                        receipt["error"] = f"HTTP {status}"
                        failure_status = "access_required" if status in (401, 403) else "unavailable"
                        retry = status in (429, 500, 502, 503, 504)
                        if "retry-after" in headers:
                            seconds = integer(headers["retry-after"])
                            if seconds is None or seconds > 30:
                                retry = False
                            else:
                                delay = max(delay, seconds)
                    elif len(body) > MAX_BYTES:
                        receipt["error"] = "Response exceeded byte budget; received prefix preserved."
                        failure_status = "partial"
                    else:
                        rows, meta = parse_mast(body)
                        receipt["mast_response"] = meta
                        if meta["status"] == "EXECUTING":
                            receipt["error"] = "MAST query is still executing."
                            failure_status, retry = "partial", True
                        else:
                            if validator:
                                validator(rows)
                            problem = page_problem(rows, meta, page, page_size, expected, seen, identity_field, page_hashes)
                            if problem:
                                receipt["error"] = problem
                                failure_status = "partial"
                                result["warnings"].append(problem)
                                # First-page rows without paging remain useful,
                                # but cannot enter the accepted-page manifest.
                                if page == 1 and problem.startswith("Missing"):
                                    all_rows.extend(rows)
                            else:
                                accepted = True
                except (OSError, ValueError, TypeError, KeyError) as error:
                    receipt["error"] = f"{type(error).__name__}: {error}"
                    retry = isinstance(error, OSError)
                receipt["finished_at_utc"] = utcnow()
                receipt_path = out / (stamp + ".receipt.json")
                atomic_json(receipt_path, receipt)
                if accepted:
                    saved = dict(receipt=receipt_path.name,
                                 receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest())
                    manifest["pages"].append(saved)
                    atomic_json(manifest_path, manifest)
                    break
                result.update(error=receipt.get("error", "MAST page was not accepted."),
                              failed_receipt=receipt_path.name, failure_status=failure_status,
                              failed_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest())
                if not retry or attempt == retries:
                    break
                sleeper(delay)
            if not accepted:
                result["status"] = "partial" if all_rows else failure_status
                break
            for key in ("error", "failed_receipt", "failure_status", "failed_receipt_sha256"):
                result.pop(key, None)
        result["page_receipts"].append(saved)
        result["mast_response"] = meta
        paging = meta["paging"]
        total, pages = integer(paging["rowsFiltered"]), integer(paging["pagesFiltered"])
        if expected is None:
            expected = (total, pages)
        result["service_total_rows"] = total
        all_rows.extend(rows)
        page_hashes.add(row_digest(rows))
        if identity_field:
            seen.update(str(row[identity_field]) for row in rows)
        result["next_page"] = page + 1
        if page >= pages:
            result["next_page"] = None
            result["query_complete"] = len(all_rows) == total and len(all_rows) <= limit
            result["status"] = ("matches" if all_rows else "no_matches") if result["query_complete"] else "partial"
            if not result["query_complete"]:
                result["warnings"].append("Record budget reached; not all returned rows are retained.")
            break
        if len(all_rows) >= limit:
            result["status"] = "partial"
            result["warnings"].append("Record budget reached; further service pages remain.")
            break
        page += 1
        if page > len(manifest["pages"]):
            sleeper(0.25)
    result.update(records=all_rows[:limit], returned_count=len(all_rows),
                  retained_count=min(len(all_rows), limit), finished_at_utc=utcnow())
    atomic_json(out / "summary.json", result)
    return result


def verify_pages(out, request=None):
    """Check every accepted receipt, raw response and page association offline."""
    out = Path(out)
    manifest = load_manifest(out, request)
    config, expected, seen, page_hashes = manifest["request"], None, set(), set()
    for page, saved in enumerate(manifest["pages"], 1):
        url = mast_url(config["service"], config["params"], page, config["page_size"])
        rows, meta = replay_page(out, saved, url, page)
        problem = page_problem(rows, meta, page, config["page_size"], expected, seen, config.get("identity_field"), page_hashes)
        if problem:
            raise ValueError("Saved accepted MAST page is inconsistent: " + problem)
        expected = (integer(meta["paging"]["rowsFiltered"]), integer(meta["paging"]["pagesFiltered"]))
        page_hashes.add(row_digest(rows))
        if config.get("identity_field"):
            seen.update(str(row[config["identity_field"]]) for row in rows)
    return len(manifest["pages"])


def verify_result(out, result, request=None):
    count = verify_pages(out, request)
    if result.get("failed_receipt"):
        receipt = json.loads(verified_bytes(out, result["failed_receipt"], result["failed_receipt_sha256"]))
        if receipt.get("raw_response"):
            verified_bytes(out, receipt["raw_response"], receipt["raw_sha256"])
    return count
