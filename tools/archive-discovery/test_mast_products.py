import json
import tempfile
import unittest
from mast_products import list_products


class ProductTests(unittest.TestCase):
    def test_relationship_paging_and_resume(self):
        payload=dict(status='COMPLETE',data=[dict(dataURI='mast:HST/product/a.fits',productFilename='a.fits')],
                     paging=dict(pagesFiltered=2,rowsFiltered=50))
        calls=[]
        def fake(url,timeout):
            calls.append(url);return 200,{},json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as d:
            report=list_products('123',d,fetcher=fake)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['records'][0]['parent_query_obsid'],'123')
            self.assertEqual(report['records'][0]['association_status'],'inherited_observation_candidate')
            self.assertEqual(list_products('123',d,resume=True,fetcher=fake),report)
            self.assertEqual(len(calls),1)
            with self.assertRaises(ValueError):list_products('other-name',d)

    def test_schema_failure_is_not_empty_success(self):
        with tempfile.TemporaryDirectory() as d:
            report=list_products('123',d,fetcher=lambda u,t:(200,{},b'{"status":"COMPLETE","data":[{}]}'))
            self.assertEqual(report['status'],'unavailable')
            self.assertIn('dataURI',report['error'])


if __name__=='__main__':unittest.main()
