#!/usr/bin/env python3
"""List one MAST observation's product metadata; never download science files."""
import argparse
import fcntl
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from archive_discovery import VERSION, atomic_json, fetch, utcnow, MAX_BYTES
from checkpoint_io import saved_directory
from mast_paging import collect_pages, mast_url, paging_request, validate_options, verify_result
from inventory_export import write_inventory


def normalise_products(rows, obsid):
    records = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('dataURI'), str) or not row['dataURI']:
            raise ValueError('MAST product lacks dataURI.')
        records.append(dict(archive='mast', record_kind='product', record_id=row['dataURI'],
            parent_query_obsid=obsid, product_observation_id=row.get('obs_id'),
            filename=row.get('productFilename'), description=row.get('description'),
            product_type=row.get('productType'), subgroup=row.get('productSubGroupDescription'),
            calibration_level=row.get('calib_level'), size_bytes=row.get('size'),
            access_status=row.get('dataRights'), product_uri=row['dataURI'],
            product_url='https://mast.stsci.edu/api/v0.1/Download/file?' + urlencode(dict(uri=row['dataURI'])),
            association_status='inherited_observation_candidate'))
    return records


def list_products(obsid, out, limit=100, resume=False, timeout=30, fetcher=fetch,
                  page_size=500, retries=2, retry_failed=False, sleeper=time.sleep):
    obsid = str(obsid)
    if not obsid.isascii() or not obsid.isdecimal():
        raise ValueError('Use the numeric observation obsid, not its obs_id name.')
    validate_options(limit, page_size, timeout, retries)
    if retry_failed and not resume:
        raise ValueError('retry-failed requires resume.')
    params = dict(obsid=obsid)
    paging_config = paging_request('Mast.Caom.Products', params, limit, page_size, timeout, retries, 'dataURI')
    request = dict(schema_version=1, tool_version=VERSION, service='Mast.Caom.Products',
                   params=params, record_limit=limit, page_size=page_size,
                   timeout_seconds=timeout, retries=retries, response_byte_limit=MAX_BYTES)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / '.writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_path = out / 'request.json'
        if request_path.exists():
            if not resume or json.loads(request_path.read_text()) != request:
                raise ValueError('Existing or different request; use --resume or a fresh directory.')
            if (out / 'report.json').exists():
                report = json.loads((out / 'report.json').read_text())
                verify_result(saved_directory(out, report['paging_directory']), report, paging_config)
                if not (retry_failed and report['status'] in ('unavailable', 'access_required', 'partial')):
                    write_inventory(report, out, out / 'report.json')
                    return report
        else:
            if resume or any(path.name != '.writer.lock' for path in out.iterdir()):
                raise ValueError('Use an empty new output directory or an existing request with --resume.')
            atomic_json(request_path, request)
        started = utcnow()
        paging = collect_pages('Mast.Caom.Products', params, out / 'pages',
            limit, page_size, timeout, retries, fetcher, sleeper,
            validator=lambda rows: normalise_products(rows, obsid), identity_field='dataURI')
        report = dict(paging, parent_obsid=obsid,
            query_url=mast_url('Mast.Caom.Products', params, 1, min(page_size, limit + 1)),
            started_at_utc=started, paging_directory='pages',
            coverage='Associated product metadata with bounded pagination; no files downloaded')
        report['records'] = normalise_products(paging['records'], obsid)
        report['warnings'] = ['Product association inherits the observation candidate; source association is unverified.'] + paging['warnings']
        atomic_json(out / 'report.json', report)
        write_inventory(report, out, out / 'report.json')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('obsid')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record-limit', type=int, default=100)
    parser.add_argument('--page-size', type=int, default=500)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    try:
        report = list_products(args.obsid, args.output, args.record_limit, args.resume,
            args.timeout, page_size=args.page_size, retries=args.retries, retry_failed=args.retry_failed)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(f"mast products: {report['status']} ({len(report['records'])} retained records)")
    print('Checkpoint:', args.output.resolve() / 'report.json')


if __name__ == '__main__':
    main()
