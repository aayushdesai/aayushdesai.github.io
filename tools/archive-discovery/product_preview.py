"""Bounded selected native-product reads, receipts and optional scientific renderer.

The standard-library service invokes the optional plotting runtime in a separate
process. No downloads occur when generating the observation overview.
"""
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from checkpoint_io import atomic_json, utcnow

ROOT = Path(__file__).resolve().parent
MAX_PRODUCT_BYTES = 10 * 1024 * 1024
HOSTS = {'mast.stsci.edu', 'archive.stsci.edu', 'dr17.sdss.org',
         'data.desi.lbl.gov', 'www.lamost.org', 'lamost.org',
         'heasarc.gsfc.nasa.gov', 'cxc.cfa.harvard.edu'}


def validate_product_url(url):
    if not isinstance(url, str):
        raise ValueError('The selected record has no native product link.')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('The selected native product host is not supported for a bounded preview; use its archive link.')
    return url


def fetch_product(url, timeout=30):
    validate_product_url(url)
    request = Request(url, headers={'User-Agent':'MusingsArchiveDiscovery/0.7.0', 'Accept':'application/fits, application/octet-stream'})
    try:
        class SafeRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                validate_product_url(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        response = build_opener(SafeRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        validate_product_url(response.geturl())
        length = response.headers.get('Content-Length')
        if length and int(length) > MAX_PRODUCT_BYTES:
            return response.status, dict(response.headers), b'', 'Native product exceeds the 10 MiB preview budget; no body was read.'
        body = response.read(MAX_PRODUCT_BYTES + 1)
        return response.status, dict(response.headers), body, None


def read_selected(record, out, fetcher=fetch_product):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out/'record.json', record)
    receipt = dict(created_at_utc=utcnow(), association=record.get('association_status','candidate_not_verified'),
        source_provenance=record.get('source_provenance'),
        warnings=['Source association is a candidate; this preview does not verify identity, footprint or detection.',
                  'Native values are displayed without reduction, fitting, resampling or cross-instrument calibration.'])
    if record.get('measurements'):
        raw = json.dumps(record['measurements'], sort_keys=True, allow_nan=False).encode()
        receipt.update(status='ready', mode='catalogue_measurements', source_hash=hashlib.sha256(raw).hexdigest(),
            source_kind='saved_catalogue_values', parent_raw_sha256=record.get('raw_sha256'), source_url=record.get('query_url'))
    else:
        url = validate_product_url(record.get('product_url'))
        receipt.update(source_url=url, mode='native_product')
        try:
            status, headers, body, error = fetcher(url, 30)
            receipt.update(http_status=status, content_type=headers.get('Content-Type'), response_bytes=len(body),
                source_hash=hashlib.sha256(body).hexdigest())
            with (out/'native.response').open('xb') as stream:
                stream.write(body)
            if status in (401,403):
                receipt.update(status='access_required', error='Native product access was denied.')
            elif status != 200:
                receipt.update(status='unavailable', error='Native product request failed.')
            elif error or len(body)>MAX_PRODUCT_BYTES:
                receipt.update(status='partial', error=error or 'Native product exceeds the byte budget; retained prefix is not parsed.')
            elif not body:
                receipt.update(status='unavailable', error='Empty native product response.')
            else:
                receipt.update(status='ready', native_file='native.response')
        except (OSError,ValueError) as error:
            receipt.update(status='unavailable', error=str(error))
    atomic_json(out/'receipt.json', receipt)
    return receipt


def renderer(mode, source, output):
    runtime = ROOT/'portal/.runtime/bin/python'
    if not runtime.is_file():
        raise ValueError('Scientific preview runtime is missing. Install portal/requirements.txt into portal/.runtime; the saved inventory remains usable.')
    try:
        result = subprocess.run([str(runtime),str(ROOT/'scientific_preview.py'),mode,str(source),str(output)],
            capture_output=True,text=True,timeout=120)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Scientific rendering exceeded its time budget; the selected source receipt remains saved.') from error
    if result.returncode:
        raise ValueError('Scientific preview could not be rendered: '+result.stderr[-1400:])
    path = Path(output)/('preview.json' if mode=='product' else 'overview.json')
    return json.loads(path.read_text())
