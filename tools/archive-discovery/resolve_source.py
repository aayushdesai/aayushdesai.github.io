"""Resolve a name through MAST with immutable attempts and explicit failed retry."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from archive_discovery import atomic_json, fetch, utcnow, validate_target, MAX_BYTES, VERSION
from checkpoint_io import new_attempt_stem, verified_bytes

STATUSES = {'resolved', 'ambiguous', 'not_found', 'unavailable', 'access_required', 'partial'}
RETRYABLE = {'unavailable', 'access_required', 'partial'}


def candidates_from_bytes(body):
    def reject_constant(value):
        raise ValueError('Name response has a nonfinite JSON constant: ' + value)
    payload = json.loads(body, parse_constant=reject_constant)
    candidates = payload.get('resolvedCoordinate') if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        raise ValueError('Name service response lacks resolvedCoordinate.')
    for row in candidates:
        if not isinstance(row, dict):
            raise ValueError('Invalid resolver candidate.')
        validate_target(float(row['ra']), float(row['decl']), 5)
    return candidates


def candidate_status(candidates):
    return 'resolved' if len(candidates) == 1 else ('ambiguous' if candidates else 'not_found')


def verify_receipt(out, saved, url, name):
    if not isinstance(saved, dict) or not isinstance(saved.get('receipt'), str) or not isinstance(saved.get('receipt_sha256'), str):
        raise ValueError('Malformed resolver receipt reference.')
    receipt = json.loads(verified_bytes(out, saved['receipt'], saved['receipt_sha256']))
    if (not isinstance(receipt, dict) or receipt.get('query_url') != url or
            receipt.get('input_name') != name or receipt.get('tool_version') != VERSION or
            not isinstance(receipt.get('status'), str) or receipt['status'] not in STATUSES or
            not isinstance(receipt.get('candidates'), list)):
        raise ValueError('Malformed resolver receipt or different query association.')
    body = None
    if receipt.get('raw_response'):
        if not isinstance(receipt.get('raw_sha256'), str):
            raise ValueError('Resolver receipt lacks its raw checksum.')
        body = verified_bytes(out, receipt['raw_response'], receipt['raw_sha256'])
    if receipt['status'] in ('resolved', 'ambiguous', 'not_found'):
        if body is None or len(body) > MAX_BYTES or receipt.get('http_status') != 200:
            raise ValueError('Successful resolver receipt lacks a valid HTTP response.')
        try:
            candidates = candidates_from_bytes(body)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('Successful resolver receipt has invalid candidate evidence.') from error
        if candidates != receipt['candidates'] or candidate_status(candidates) != receipt['status']:
            raise ValueError('Resolver receipt differs from its raw candidate evidence.')
    elif receipt['candidates']:
        raise ValueError('Failed resolver receipt must not select candidate coordinates.')
    return receipt


def load_attempts(out, url, name):
    path = out / 'attempts.json'
    if path.exists():
        manifest = json.loads(path.read_text())
        if (not isinstance(manifest, dict) or manifest.get('query_url') != url or
                manifest.get('schema_version') != 1 or not isinstance(manifest.get('attempts'), list)):
            raise ValueError('Malformed or different resolver attempt manifest.')
    else:
        manifest = dict(schema_version=1, query_url=url, created_at_utc=utcnow(), attempts=[])
    receipts, known = {}, set()
    for saved in manifest['attempts']:
        receipt = verify_receipt(out, saved, url, name)
        if saved['receipt'] in known:
            raise ValueError('Resolver manifest repeats a receipt.')
        known.add(saved['receipt'])
        receipts[saved['receipt']] = receipt
    # A complete receipt can survive interruption immediately before manifest
    # publication. Recover only validated files in this shallow output directory.
    changed = not path.exists()
    for receipt_path in sorted(out.glob('*.receipt.json')):
        if receipt_path.name in known:
            continue
        if not receipt_path.name.startswith('resolver-'):
            raise ValueError('Unexpected resolver receipt; preserve this directory for inspection.')
        saved = dict(receipt=receipt_path.name, receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest())
        receipts[saved['receipt']] = verify_receipt(out, saved, url, name)
        manifest['attempts'].append(saved)
        known.add(saved['receipt'])
        changed = True
    if changed:
        atomic_json(path, manifest)
    return manifest, receipts


def report_from_receipt(receipt, saved):
    return dict(receipt, receipt_file=saved['receipt'], receipt_sha256=saved['receipt_sha256'])


def resolve_name(name, out, resume=False, timeout=30, fetcher=fetch, retry_failed=False):
    if not isinstance(name, str) or not name.strip() or len(name) > 512:
        raise ValueError('Supply a nonempty source name of at most 512 characters.')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('Require 0 < timeout <= 120.')
    if retry_failed and not resume:
        raise ValueError('retry-failed requires resume.')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    api_request = dict(service='Mast.Name.Lookup', params=dict(input=name, format='json'))
    request = dict(schema_version=2, tool_version=VERSION, api_request=api_request,
                   timeout_seconds=timeout, response_byte_limit=MAX_BYTES)
    url = 'https://mast.stsci.edu/api/v0/invoke?' + urlencode(dict(request=json.dumps(api_request)))
    with (out / '.writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_path = out / 'request.json'
        report_path = out / 'report.json'
        if request_path.exists():
            if not resume or json.loads(request_path.read_text()) != request:
                raise ValueError('Resolver checkpoint exists or differs; use --resume or a fresh directory.')
        else:
            if resume:
                raise ValueError('No resolver checkpoint to resume.')
            if any(path.name != '.writer.lock' for path in out.iterdir()):
                raise ValueError('Resolver output directory must be empty.')
            atomic_json(request_path, request)
        manifest, receipts = load_attempts(out, url, name)
        if report_path.exists():
            report = json.loads(report_path.read_text())
            if not isinstance(report, dict):
                raise ValueError('Malformed resolver report.')
            saved = dict(receipt=report.get('receipt_file'), receipt_sha256=report.get('receipt_sha256'))
            if saved not in manifest['attempts'] or report != report_from_receipt(receipts.get(saved['receipt'], {}), saved):
                raise ValueError('Resolver report differs from its verified receipt.')
        if manifest['attempts']:
            saved = manifest['attempts'][-1]
            report = report_from_receipt(receipts[saved['receipt']], saved)
            if not report_path.exists() or json.loads(report_path.read_text()) != report:
                atomic_json(report_path, report)
            if not (retry_failed and report['status'] in RETRYABLE):
                return report
        stamp = new_attempt_stem(out, 'resolver')
        receipt = dict(input_name=name, query_url=url, tool_version=VERSION,
                       started_at_utc=utcnow(), status='unavailable', candidates=[])
        interrupted = False
        try:
            status, headers, body = fetcher(url, timeout)
            raw = out / (stamp + '.response')
            with raw.open('xb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            receipt.update(http_status=status, response_headers=headers, raw_response=raw.name,
                           response_bytes=len(body), raw_sha256=hashlib.sha256(body).hexdigest())
            if status != 200:
                receipt['status'] = 'access_required' if status in (401, 403) else 'unavailable'
                receipt['error'] = f'HTTP {status}'
            elif len(body) > MAX_BYTES:
                receipt['status'] = 'partial'
                receipt['error'] = 'Name response byte budget exceeded; prefix preserved.'
            else:
                candidates = candidates_from_bytes(body)
                receipt['candidates'] = candidates
                receipt['status'] = candidate_status(candidates)
        except (OSError, ValueError, TypeError, KeyError) as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
        except KeyboardInterrupt:
            interrupted = True
            receipt.update(status='unavailable', candidates=[])
            receipt['error'] = 'Interrupted before name resolution completed.'
        receipt['finished_at_utc'] = utcnow()
        receipt_path = out / (stamp + '.receipt.json')
        atomic_json(receipt_path, receipt)
        saved = dict(receipt=receipt_path.name, receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest())
        manifest['attempts'].append(saved)
        atomic_json(out / 'attempts.json', manifest)
        report = report_from_receipt(receipt, saved)
        atomic_json(report_path, report)
        if interrupted:
            raise KeyboardInterrupt()
        return report
