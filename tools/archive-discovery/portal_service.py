#!/usr/bin/env python3
"""Loopback target-data portal backed by the portable metadata engine.

Queries append receipts. Selected ready products have a 10 MiB read budget.
No reductions or credentials are handled and native holdings are not modified.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
import uuid

import archive_discovery as archive
from checkpoint_io import atomic_json, utcnow
from inventory_export import write_inventory
from mast_products import list_products
from resolve_source import resolve_name
from product_preview import read_selected, renderer, fetch_product

ROOT = Path(__file__).resolve().parent
RUN_ID = re.compile(r'^[0-9a-f]{32}$')
EXPORTS = {'report.json', 'records.csv', 'archives.csv', 'export.json'}


def flatten(report):
    if not report:
        return []
    return [record for result in report['archives'].values() for record in result['records']] if 'archives' in report else report.get('records', [])


class Portal:
    def __init__(self, root=None, fetcher=None, product_fetcher=None):
        self.root = Path(root) if root is not None else ROOT / 'portal' / 'runs'
        self.root.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or archive.fetch
        self.product_fetcher = product_fetcher or fetch_product
        self.jobs = {}
        for path in self.root.glob('*/job.json'):
            try:
                saved=json.loads(path.read_text())
                if RUN_ID.fullmatch(path.parent.name) and saved.get('run_id')==path.parent.name and saved.get('kind')=='preview' and saved.get('phase')=='complete':
                    self.jobs[saved['run_id']]=saved
            except (OSError,ValueError,TypeError):
                continue
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)

    def archives(self):
        registry = None
        try:
            from coverage_registry import load_registry
            registry = load_registry()
        except (ImportError, OSError, ValueError):
            pass
        sources = registry.get('sources', []) if isinstance(registry, dict) else []
        items = []
        for name in archive.ARCHIVES:
            related = [source for source in sources if source.get('archive') == name]
            items.append(dict(id=name, label=name.upper() if name in ('mast', 'eso', 'gtc', 'sdss', 'desi', 'lamost') else name.title(),
                implemented=name in archive.IMPLEMENTED, status='available' if name in archive.IMPLEMENTED else 'unsupported',
                capabilities=[source.get('capabilities', {}) for source in related],
                limits=[source.get('limits', []) for source in related]))
        return dict(archives=items, registry=registry,
            limitations=['Matches retain their candidate association; detector, slit and source coverage are not verified.',
                         'Positions are ICRS degrees; a supplied epoch is recorded, without proper-motion propagation.',
                         'Only the displayed archives, releases and methods are searched; completeness is bounded by access and query limits.'])

    def _new(self, kind, details):
        with self.lock:
            if sum(job['phase'] in ('queued', 'running') for job in self.jobs.values()) >= 8:
                raise ValueError('The local query queue is full; wait for a current query to finish.')
            identity = uuid.uuid4().hex
            path = self.root / identity
            path.mkdir(exist_ok=False)
            job = dict(run_id=identity, kind=kind, phase='queued', created_at_utc=utcnow(), **details)
            self.jobs[identity] = job
            atomic_json(path / 'job.json', job)
            return identity

    def _update(self, identity, **values):
        with self.lock:
            self.jobs[identity].update(values)
            atomic_json(self.root / identity / 'job.json', self.jobs[identity])

    def snapshot(self, identity):
        if not RUN_ID.fullmatch(identity):
            raise ValueError('Invalid query identifier.')
        with self.lock:
            if identity not in self.jobs:
                path = self.root / identity / 'job.json'
                if not path.exists():
                    raise KeyError('Query not found.')
                job = json.loads(path.read_text())
                if not isinstance(job, dict) or job.get('run_id') != identity:
                    raise ValueError('Saved query metadata does not match its identifier.')
                if job.get('phase') in ('queued', 'running'):
                    job.update(phase='failed', error='The service stopped during this query; its checkpoint remains preserved. Start a fresh query.')
            else:
                job = dict(self.jobs[identity])
        output = self.root / identity
        if job['kind'] == 'resolution':
            if (output / 'resolution/report.json').exists():
                job['resolution'] = json.loads((output / 'resolution/report.json').read_text())
        elif job['kind'] == 'preview':
            preview = output / 'plots/preview.json'
            receipt = output / 'product/receipt.json'
            if preview.exists():
                job['preview'] = json.loads(preview.read_text())
            elif receipt.exists():
                job['preview'] = json.loads(receipt.read_text())
                if job['phase']=='failed' and job['preview'].get('status')=='ready':
                    job['preview'].update(native_status='ready',status='unsupported',error=job.get('error'))
        else:
            if (output / 'query/report.json').exists():
                job['report'] = json.loads((output / 'query/report.json').read_text())
                job['records'] = flatten(job['report'])
        return job

    def resolve(self, payload):
        name = payload.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 512:
            raise ValueError('Enter a source name of at most 512 characters.')
        identity = self._new('resolution', dict(input_name=name, mode='live_metadata'))
        def work():
            self._update(identity, phase='running')
            try:
                report = resolve_name(name, self.root / identity / 'resolution', timeout=20, fetcher=self.fetcher)
                self._update(identity, phase='resolved' if report['status'] in ('resolved', 'ambiguous', 'not_found') else 'failed',
                    resolution_status=report['status'], error=report.get('error'))
            except (OSError, ValueError, TypeError) as error:
                self._update(identity, phase='failed', error=str(error))
        self.executor.submit(work)
        return dict(run_id=identity, phase='queued')

    def search(self, payload):
        epoch = payload.get('epoch')
        epoch = None if epoch in (None, '') else float(epoch)
        radius = float(payload.get('radius_arcsec', 5))
        if payload.get('resolution_id'):
            if payload.get('confirmed') is not True:
                raise ValueError('Confirm the resolved source identity before searching.')
            resolved = self.snapshot(payload['resolution_id'])
            candidates = resolved.get('resolution', {}).get('candidates', [])
            index = payload.get('candidate_index')
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(candidates):
                raise ValueError('Select a valid resolved coordinate candidate.')
            candidate = candidates[index]
            target = archive.validate_target(float(candidate['ra']), float(candidate['decl']), radius, epoch)
            target.update(input_name=resolved.get('input_name'), canonical_name=candidate.get('canonicalName'),
                          resolver=candidate.get('resolver'), identity_confirmed=True,
                          resolution_sha256=resolved['resolution'].get('raw_sha256'),
                          resolution_run_id=payload['resolution_id'])
        else:
            target = archive.validate_target(float(payload.get('ra')), float(payload.get('dec')), radius, epoch)
        selected = payload.get('archives', list(archive.ARCHIVES))
        if not isinstance(selected, list) or not selected or any(not isinstance(name, str) or name not in archive.ARCHIVES for name in selected):
            raise ValueError('Select one or more supported archive identifiers.')
        selected = list(dict.fromkeys(selected))
        limit = payload.get('record_limit', 200)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError('The record limit must be an integer from 1 to 10000.')
        identity = self._new('search', dict(target=target, archives=selected, mode='live_metadata', record_limit=limit))
        def work():
            self._update(identity, phase='running')
            try:
                report = archive.run(target, selected, self.root / identity / 'query', timeout=20, limit=limit,
                    page_size=min(limit + 1, 500), retries=0, fetcher=self.fetcher)
                self._update(identity, phase='complete', archive_statuses={name: value['status'] for name, value in report['archives'].items()})
            except (OSError, ValueError, TypeError) as error:
                self._update(identity, phase='failed', error=str(error))
        self.executor.submit(work)
        return dict(run_id=identity, phase='queued')

    def products(self, payload):
        source = self.snapshot(str(payload.get('run_id', '')))
        obsid = str(payload.get('obsid', ''))
        if not any(row.get('archive') == 'mast' and row.get('product_query_obsid') == obsid for row in source.get('records', [])):
            raise ValueError('Choose a MAST observation from this saved search.')
        identity = self._new('products', dict(parent_run_id=source['run_id'], parent_obsid=obsid, mode='live_metadata'))
        def work():
            self._update(identity, phase='running')
            try:
                report = list_products(obsid, self.root / identity / 'query', limit=200, page_size=200,
                    timeout=20, retries=0, fetcher=self.fetcher)
                self._update(identity, phase='complete', archive_statuses={'mast': report['status']})
            except (OSError, ValueError, TypeError) as error:
                self._update(identity, phase='failed', error=str(error))
        self.executor.submit(work)
        return dict(run_id=identity, phase='queued')

    def preview(self, payload):
        source = self.snapshot(str(payload.get('run_id', '')))
        index = payload.get('record_index')
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(source.get('records', [])):
            raise ValueError('Choose a saved measurement or native product record.')
        record = source['records'][index]
        if not record.get('measurements') and not record.get('product_url'):
            raise ValueError('This record has no numerical measurements or ready native product URL; list its archive products first.')
        source_hash=hashlib.sha256((self.root/source['run_id']/'query/report.json').read_bytes()).hexdigest()
        with self.lock:
            for cached in self.jobs.values():
                if cached.get('kind')=='preview' and cached.get('parent_run_id')==source['run_id'] and cached.get('record_index')==index and cached.get('parent_report_sha256')==source_hash and cached['phase'] in ('queued','running','complete'):
                    if cached['phase']=='complete':
                        product=self.root/cached['run_id']/'product';receipt=json.loads((product/'receipt.json').read_text())
                        if receipt.get('native_file') and hashlib.sha256((product/receipt['native_file']).read_bytes()).hexdigest()!=receipt['source_hash']:
                            raise ValueError('Cached native product checksum differs; preserve this checkpoint for review.')
                    return dict(run_id=cached['run_id'],phase=cached['phase'],reused=True)
        record=dict(record)
        result=source['report'].get('archives',{}).get(record.get('archive'),{})
        provenance=dict(parent_report_sha256=source_hash,archive_receipt_sha256=result.get('receipt_sha256'),raw_sha256=result.get('raw_sha256'))
        for branch in result.get('branches',[]):
            if branch.get('route_id')==record.get('release_branch'):
                provenance.update(raw_sha256=branch.get('raw_sha256'),query_url=branch.get('query_url'))
        record['source_provenance']=provenance
        identity = self._new('preview', dict(parent_run_id=source['run_id'], record_index=index,
            mode='saved_catalogue_preview' if record.get('measurements') else 'selected_native_product',
            parent_report_sha256=source_hash))
        def work():
            self._update(identity, phase='running')
            try:
                fetcher = self.product_fetcher
                receipt = read_selected(record, self.root/identity/'product', fetcher)
                if source.get('mode') == 'offline_native_control':
                    receipt.update(mode='saved_native_control' if record.get('product_url') else 'saved_catalogue_control',
                        parent_control_sha256=source.get('fixture_source_sha256'))
                    atomic_json(self.root/identity/'product/receipt.json',receipt)
                if receipt['status'] != 'ready':
                    self._update(identity, phase='failed', error=receipt.get('error'), preview_status=receipt['status'])
                else:
                    renderer('product', self.root/identity/'product', self.root/identity/'plots')
                    self._update(identity, phase='complete')
            except (OSError, ValueError, TypeError) as error:
                self._update(identity, phase='failed', error=str(error))
        self.executor.submit(work)
        return dict(run_id=identity, phase='queued')

    def overview(self, identity):
        source = self.snapshot(identity)
        if not source.get('report'):
            raise ValueError('A saved search report is required for the data inventory.')
        path = self.root/identity/'overview'
        with self.lock:
            if not (path/'overview.svg').exists():
                renderer('overview', self.root/identity/'query/report.json', path)
        return path/'overview.svg'

    def demo(self):
        raise ValueError('Private saved examples are not included in the public source package. Search a source to create a local inventory.')

    def demo_products(self, payload):
        return self.demo()

    def close(self):
        self.executor.shutdown(wait=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'TargetDataPortal/0.7.0'

    def _origin_ok(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in hosts and (origin is None or origin in {f'http://{name}' for name in hosts})

    def _send(self, status, body, kind='application/json; charset=utf-8', attachment=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, dict(error='This service accepts its loopback origin only.'))
        parts = urlsplit(self.path).path.strip('/').split('/')
        try:
            if parts == ['api', 'archives']:
                return self._send(200, self.server.portal.archives())
            if len(parts) == 3 and parts[:2] == ['api', 'runs']:
                return self._send(200, self.server.portal.snapshot(parts[2]))
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'overview.svg':
                return self._send(200, self.server.portal.overview(parts[2]).read_bytes(), 'image/svg+xml')
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'native.fits':
                job=self.server.portal.snapshot(parts[2])
                if job['kind']!='preview':raise ValueError('A saved native preview is required.')
                path=self.server.portal.root/job['run_id']/'product'
                receipt=json.loads((path/'receipt.json').read_text())
                if receipt.get('status')!='ready' or receipt.get('native_file')!='native.response':
                    raise ValueError('No complete native file is available in this preview.')
                body=(path/'native.response').read_bytes()
                if hashlib.sha256(body).hexdigest()!=receipt['source_hash']:raise ValueError('Cached native product checksum differs.')
                return self._send(200,body,'application/fits','native.fits')
            if len(parts) == 5 and parts[:2] == ['api', 'runs'] and parts[3] == 'plots':
                job = self.server.portal.snapshot(parts[2])
                name = parts[4]
                if job['kind'] != 'preview' or not re.fullmatch(r'(?:series-\d+|measurements-\d+)\.(?:svg|png|pdf|csv)|(?:measurements|preview)\.json', name):
                    raise ValueError('Invalid scientific preview artifact.')
                path = self.server.portal.root/job['run_id']/'plots'/name
                kinds={'.svg':'image/svg+xml','.png':'image/png','.pdf':'application/pdf','.csv':'text/csv','.json':'application/json'}
                return self._send(200,path.read_bytes(),kinds[path.suffix],name if path.suffix in ('.pdf','.csv','.json') else None)
            if len(parts) == 5 and parts[:2] == ['api', 'runs'] and parts[3] == 'exports' and parts[4] in EXPORTS:
                job = self.server.portal.snapshot(parts[2])
                path = self.server.portal.root / job['run_id'] / 'query' / parts[4]
                body = path.read_bytes()
                return self._send(200, body, 'text/csv; charset=utf-8' if path.suffix == '.csv' else 'application/json', path.name)
            static = {'': 'index.html', 'index.html': 'index.html', 'app.js': 'app.js', 'style.css': 'style.css'}
            key = '/'.join(parts)
            if key in static:
                path = ROOT / 'portal' / 'static' / static[key]
                kind = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}[path.suffix]
                return self._send(200, path.read_bytes(), kind)
            return self._send(404, dict(error='Page not found.'))
        except (OSError, KeyError):
            return self._send(404, dict(error='Saved query or export not found.'))
        except (ValueError, TypeError) as error:
            return self._send(400, dict(error=str(error)))

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, dict(error='This service accepts its loopback origin only.'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Supply a small JSON request body.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Request body must be an object.')
            routes = {'/api/resolve': self.server.portal.resolve, '/api/search': self.server.portal.search,
                      '/api/products': self.server.portal.products, '/api/preview': self.server.portal.preview,
                      '/api/demo-products': self.server.portal.demo_products,
                      '/api/demo': lambda value: self.server.portal.demo()}
            route = routes.get(urlsplit(self.path).path)
            if route is None:
                return self._send(404, dict(error='Endpoint not found.'))
            return self._send(202, route(payload))
        except (OSError, KeyError, ValueError, TypeError) as error:
            return self._send(400, dict(error=str(error)))

    def log_message(self, format, *args):
        pass


def make_server(port=8765, root=None, fetcher=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.portal = Portal(root, fetcher)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--runs', type=Path, default=ROOT / 'portal' / 'runs')
    args = parser.parse_args()
    server = make_server(args.port, args.runs)
    print(f'Target data portal: http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.portal.close()


if __name__ == '__main__':
    main()
