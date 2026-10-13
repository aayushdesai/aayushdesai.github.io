"""Offline command-engine checks for paged observations and product lists."""
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import archive_discovery as archive
from mast_products import list_products


def page_request(url):
    return json.loads(parse_qs(urlsplit(url).query)['request'][0])


def response(rows, request, total):
    size = request['pagesize']
    payload = dict(status='COMPLETE', data=rows, paging=dict(
        page=request['page'], pageSize=size, rows=len(rows),
        rowsFiltered=total, pagesFiltered=(total + size - 1) // size))
    return 200, {}, json.dumps(payload).encode()


class MastIntegrationTests(unittest.TestCase):
    def test_observation_export_and_verified_resume(self):
        calls = []
        target = archive.validate_target(0, 0, 5, 2016)
        rows = [dict(obsid=9007199254740993 + index, obs_id=f'hst-{index}',
                     s_ra=0 if index == 0 else 1, s_dec=0,
                     s_region='POLYGON ICRS ...') for index in range(3)]
        def fetch(url, timeout):
            calls.append(url)
            request = page_request(url)
            self.assertEqual(request['service'], 'Mast.Caom.Cone')
            self.assertEqual(request['params']['radius'], 5 / 3600)
            start = (request['page'] - 1) * request['pagesize']
            return response(rows[start:start + request['pagesize']], request, len(rows))
        with tempfile.TemporaryDirectory() as directory:
            first = archive.run(target, ['mast', 'gtc'], directory, limit=5,
                page_size=2, retries=0, fetcher=fetch, sleeper=lambda delay: None)
            result = first['archives']['mast']
            self.assertEqual(result['status'], 'matches')
            self.assertTrue(result['query_complete'])
            self.assertEqual(len(result['page_receipts']), 2)
            self.assertEqual(result['records'][0]['record_id'], '9007199254740993')
            self.assertFalse(result['records'][1]['within_requested_cone'])
            self.assertFalse(result['records'][1]['footprint_verified'])
            self.assertEqual(result['records'][1]['association_status'], 'candidate_not_verified')
            self.assertEqual(first['archives']['gtc']['status'], 'unsupported')
            exported = list(csv.DictReader(io.StringIO((Path(directory) / 'records.csv').read_text())))
            self.assertEqual(len(exported), 3)
            self.assertEqual(exported[0]['record_id'], '9007199254740993')
            self.assertEqual(exported[0]['query_status'], 'matches')
            self.assertEqual(exported[0]['query_complete'], 'true')
            statuses = list(csv.DictReader(io.StringIO((Path(directory) / 'archives.csv').read_text())))
            self.assertEqual({row['archive']: row['status'] for row in statuses}, {'mast': 'matches', 'gtc': 'unsupported'})
            self.assertEqual(json.loads(statuses[0]['request_metadata'])['target']['position_epoch_jyear'], 2016)
            second = archive.run(target, ['mast', 'gtc'], directory, resume=True,
                limit=5, page_size=2, retries=0, fetcher=fetch, sleeper=lambda delay: None)
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 2)
            manifest = json.loads((Path(directory) / 'mast-pages/manifest.json').read_text())
            receipt = json.loads((Path(directory) / 'mast-pages' / manifest['pages'][0]['receipt']).read_text())
            raw = Path(directory) / 'mast-pages' / receipt['raw_response']
            raw.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                archive.run(target, ['mast', 'gtc'], directory, resume=True, limit=5,
                    page_size=2, retries=0, fetcher=fetch, sleeper=lambda delay: None)
            self.assertEqual(len(calls), 2)

    def test_later_page_retry_does_not_repeat_accepted_observation_page(self):
        calls = []
        failed = [False]
        def fetch(url, timeout):
            request = page_request(url)
            page = request['page']
            calls.append(page)
            if page == 2 and not failed[0]:
                failed[0] = True
                return 503, {}, b'service unavailable'
            return response([dict(obsid=page)], request, 2)
        with tempfile.TemporaryDirectory() as directory:
            target = archive.validate_target(0, 0, 5)
            first = archive.run(target, ['mast'], directory, limit=5, page_size=1,
                retries=0, fetcher=fetch, sleeper=lambda delay: None)['archives']['mast']
            self.assertEqual(first['status'], 'partial')
            self.assertEqual(first['retained_count'], 1)
            self.assertEqual(first['failure_status'], 'unavailable')
            second = archive.run(target, ['mast'], directory, resume=True, retry_failed=True,
                limit=5, page_size=1, retries=0, fetcher=fetch, sleeper=lambda delay: None)['archives']['mast']
            self.assertEqual(second['status'], 'matches')
            self.assertTrue(second['query_complete'])
            self.assertEqual(calls, [1, 2, 2])
            self.assertNotIn('error', second)
            self.assertTrue((Path(directory) / 'mast-pages' / first['failed_receipt']).exists())

    def test_product_pages_keep_exact_parent_and_distinct_products(self):
        calls = []
        rows = [dict(dataURI=f'mast:HST/product/{name}.fits', productFilename=f'{name}.fits',
                     obs_id='human-name', productType='SCIENCE') for name in ('raw', 'reduced', 'alternate')]
        parent = '9007199254740993'
        def fetch(url, timeout):
            request = page_request(url)
            calls.append(request['page'])
            self.assertEqual(request['service'], 'Mast.Caom.Products')
            self.assertEqual(request['params']['obsid'], parent)
            start = (request['page'] - 1) * request['pagesize']
            return response(rows[start:start + request['pagesize']], request, len(rows))
        with tempfile.TemporaryDirectory() as directory:
            report = list_products(parent, directory, limit=5, page_size=2, retries=0,
                fetcher=fetch, sleeper=lambda delay: None)
            self.assertEqual(report['status'], 'matches')
            self.assertTrue(report['query_complete'])
            self.assertEqual(report['retained_count'], 3)
            self.assertEqual({row['parent_query_obsid'] for row in report['records']}, {parent})
            self.assertEqual({row['association_status'] for row in report['records']}, {'inherited_observation_candidate'})
            exported = list(csv.DictReader(io.StringIO((Path(directory) / 'records.csv').read_text())))
            self.assertEqual(len(exported), 3)
            self.assertEqual({row['parent_query_obsid'] for row in exported}, {parent})
            self.assertEqual({row['product_type'] for row in exported}, {'SCIENCE'})
            self.assertEqual(list_products(parent, directory, resume=True, limit=5,
                page_size=2, retries=0, fetcher=fetch, sleeper=lambda delay: None), report)
            self.assertEqual(calls, [1, 2])

    def test_product_retry_distinguishes_restricted_from_empty(self):
        calls = []
        def fetch(url, timeout):
            request = page_request(url)
            calls.append(request['page'])
            if len(calls) == 1:
                return 403, {}, b'access required'
            return response([], request, 0)
        with tempfile.TemporaryDirectory() as directory:
            first = list_products('123', directory, retries=0, fetcher=fetch, sleeper=lambda delay: None)
            self.assertEqual(first['status'], 'access_required')
            self.assertFalse(first['query_complete'])
            second = list_products('123', directory, resume=True, retry_failed=True,
                retries=0, fetcher=fetch, sleeper=lambda delay: None)
            self.assertEqual(second['status'], 'no_matches')
            self.assertTrue(second['query_complete'])
            self.assertEqual(calls, [1, 1])
            receipt_path = Path(directory) / 'pages' / first['failed_receipt']
            self.assertEqual(hashlib.sha256(receipt_path.read_bytes()).hexdigest(), first['failed_receipt_sha256'])

    def test_product_interruption_recovers_next_page(self):
        calls = []
        def fetch(url, timeout):
            request = page_request(url)
            calls.append(request['page'])
            if calls == [1, 2]:
                raise KeyboardInterrupt()
            return response([dict(dataURI=f"mast:HST/product/{request['page']}.fits")], request, 2)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                list_products('123', directory, limit=5, page_size=1, retries=0,
                    fetcher=fetch, sleeper=lambda delay: None)
            report = list_products('123', directory, resume=True, limit=5, page_size=1,
                retries=0, fetcher=fetch, sleeper=lambda delay: None)
            self.assertTrue(report['query_complete'])
            self.assertEqual(calls, [1, 2, 2])


if __name__ == '__main__':
    unittest.main()
