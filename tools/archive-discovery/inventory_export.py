#!/usr/bin/env python3
"""Export saved discovery metadata and explicit archive statuses without queries.

Nested CSV values use JSON. The source report remains the typed record and
receipt reference; exporting it does not validate a source association.
"""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path

from checkpoint_io import atomic_json, utcnow

STATUSES = {'matches', 'no_matches', 'partial', 'unavailable', 'access_required', 'unsupported'}
CONTEXT = ('query_status', 'query_complete', 'query_url', 'query_coverage')
RECORD_COLUMNS = ('archive', 'record_id', 'record_kind', 'observation_id', 'parent_query_obsid',
                  'target_name', 'instrument', 'product_type', 'filter_name', 'observed_at',
                  'observation_mjd', 'exposure_seconds', 'ra_deg', 'dec_deg', 'separation_arcsec',
                  'association_status', 'access_status', 'product_url')
ARCHIVE_COLUMNS = ('archive', 'status', 'query_complete', 'coverage', 'returned_count',
                   'retained_count', 'exported_record_count', 'service_total_rows',
                   'failure_status', 'error', 'query_url', 'started_at_utc', 'finished_at_utc')


def cell(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list, tuple, bool)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('CSV values must be finite or null.')
    if not isinstance(value, (str, int, float)):
        raise ValueError('Unsupported metadata value in CSV.')
    return str(value)


def csv_bytes(rows, preferred):
    keys = set(preferred)
    for row in rows:
        if any(not isinstance(key, str) for key in row):
            raise ValueError('Metadata keys must be strings.')
        keys.update(row)
    columns = list(preferred) + sorted(keys.difference(preferred))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: cell(value) for key, value in row.items()})
    return buffer.getvalue().encode('utf-8')


def inventory_rows(report):
    if not isinstance(report, dict):
        raise ValueError('Expected a discovery or product report object.')
    if 'request' in report and not isinstance(report['request'], dict):
        raise ValueError('Report request must be an object.')
    if 'archives' in report:
        results = report['archives']
        if not isinstance(results, dict) or not results:
            raise ValueError('Discovery report must contain archive results.')
        requested = report.get('request', {}).get('archives')
        if requested is not None and (not isinstance(requested, list) or
                set(requested) != set(results) or len(requested) != len(results)):
            raise ValueError('Report archives differ from the requested archive list.')
    elif 'parent_obsid' in report:
        if not isinstance(report['parent_obsid'], (str, int)) or isinstance(report['parent_obsid'], bool):
            raise ValueError('Product report must identify its parent observation.')
        results = {'mast': report}
    else:
        raise ValueError('Report lacks archive results or parent_obsid.')
    records, archives = [], []
    for name, result in results.items():
        if not isinstance(name, str) or not name or not isinstance(result, dict):
            raise ValueError('Invalid archive result.')
        if result.get('status') not in STATUSES:
            raise ValueError('Invalid or missing archive status.')
        if result.get('archive', name) != name:
            raise ValueError('Result archive disagrees with its report key.')
        native_records = result.get('records')
        if not isinstance(native_records, list) or any(not isinstance(row, dict) for row in native_records):
            raise ValueError('Archive result must contain a record list.')
        for native in native_records:
            if native.get('archive', name) != name:
                raise ValueError('Record archive disagrees with its report key.')
            if any(key in native for key in CONTEXT):
                raise ValueError('Record metadata collides with query context columns.')
            row = dict(native)
            row['archive'] = name
            row.update(query_status=result.get('status'), query_complete=result.get('query_complete'),
                       query_url=result.get('query_url'), query_coverage=result.get('coverage'))
            records.append(row)
        summary = {key: value for key, value in result.items() if key != 'records'}
        if 'exported_record_count' in summary or 'request_metadata' in summary:
            raise ValueError('Archive metadata collides with export context columns.')
        summary.update(archive=name, exported_record_count=len(native_records))
        if 'request' in report:
            summary['request_metadata'] = report['request']
        archives.append(summary)
    return records, archives


def atomic_bytes(path, body):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_inventory(report, out, source_report_path=None):
    """Write derived tables. Caller owns the output writer lock when in-place."""
    out = Path(out)
    records, archives = inventory_rows(report)
    bodies = {'records.csv': csv_bytes(records, RECORD_COLUMNS + CONTEXT),
              'archives.csv': csv_bytes(archives, ARCHIVE_COLUMNS)}
    manifest = dict(schema_version=1, generated_at_utc=utcnow(), record_count=len(records),
                    archive_count=len(archives), nested_csv_values='JSON', null_csv_values='empty cell',
                    files={name: dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                           for name, body in bodies.items()})
    if source_report_path is not None:
        source = Path(source_report_path)
        if source.resolve() in {(out / name).resolve() for name in ('records.csv', 'archives.csv', 'export.json')}:
            raise ValueError('Source report path collides with an output file; use a fresh directory.')
        raw = source.read_bytes()
        if json.loads(raw) != report:
            raise ValueError('Source report bytes differ from the report being exported.')
        manifest['source_report'] = dict(path=str(source.resolve()), sha256=hashlib.sha256(raw).hexdigest())
    out.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        atomic_bytes(out / name, body)
    atomic_json(out / 'export.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_bytes())
        args.output.mkdir(parents=True, exist_ok=False)
        with (args.output / '.writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest = write_inventory(report, args.output, args.report)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, str(error) + '\n')
    print(f"Exported {manifest['record_count']} records and {manifest['archive_count']} archive statuses.")
    print('Inventory:', args.output.resolve())


if __name__ == '__main__':
    main()
