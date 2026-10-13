import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit, parse_qs
import archive_discovery as archive


class MastTests(unittest.TestCase):
    def test_observation_id_is_not_product_query_key(self):
        row = dict(obsid=9007199254740993, obs_id='hst-human-name', s_ra=0, s_dec=0,
                   dataURL='mast:HST/product/example.fits', t_min=None)
        r = archive.normalise('mast', [row], archive.validate_target(0,0,5))[0]
        self.assertEqual(r['product_query_obsid'], '9007199254740993')
        self.assertEqual(r['observation_id'], 'hst-human-name')
        self.assertEqual(r['record_kind'], 'observation')
        self.assertIsNone(r['observation_mjd'])
        self.assertIn('uri=mast%3AHST', r['product_url'])

    def test_executing_and_paged_queries_remain_partial(self):
        for payload in [dict(status='EXECUTING'), dict(status='COMPLETE', data=[], paging=dict(pagesFiltered=2))]:
            with tempfile.TemporaryDirectory() as directory:
                r = archive.search_archive('mast',archive.validate_target(0,0,5),Path(directory),1,20,
                    lambda u,t:(200,{},json.dumps(payload).encode()))
                self.assertEqual(r['status'],'partial')
                self.assertFalse(r['query_complete'])

    def test_error_and_empty_success_are_distinct(self):
        for payload in [dict(status='ERROR',data=[]),dict(status='COMPLETE')]:
            with self.assertRaises(ValueError):
                archive.parse_mast(json.dumps(payload).encode())
        rows, meta = archive.parse_mast(b'{"status":"COMPLETE","data":[],"paging":{"pagesFiltered":0}}')
        self.assertEqual(rows,[])
        self.assertFalse(meta['incomplete'])

    def test_degrees_and_page_parameters(self):
        url=archive.query_url('mast',archive.validate_target(1,2,36),20)
        request=json.loads(parse_qs(urlsplit(url).query)['request'][0])
        self.assertEqual(request['params']['radius'],0.01)
        self.assertEqual((request['page'],request['pagesize']),(1,21))


if __name__ == '__main__':
    unittest.main()
