import json
from pathlib import Path
import tempfile
import unittest
from resolve_source import resolve_name


class ResolverTests(unittest.TestCase):
    def test_ambiguity_is_not_silently_selected(self):
        candidate=dict(ra=1,decl=2,canonicalName='a',resolver='SIMBAD')
        for rows,status in [([], 'not_found'),([candidate], 'resolved'),([candidate,candidate], 'ambiguous')]:
            with tempfile.TemporaryDirectory() as d:
                r=resolve_name('source',d,fetcher=lambda u,t:(200,{},json.dumps(dict(resolvedCoordinate=rows)).encode()))
                self.assertEqual(r['status'],status)

    def test_resume_reuses_verified_response(self):
        with tempfile.TemporaryDirectory() as d:
            first=resolve_name('source',d,fetcher=lambda u,t:(200,{},b'{"resolvedCoordinate":[{"ra":1,"decl":2}]}'))
            def forbidden(*args):
                self.fail('Resume repeated the network request')
            self.assertEqual(first,resolve_name('source',d,resume=True,fetcher=forbidden))
            with self.assertRaises(ValueError):resolve_name('different',d,resume=True)
            (Path(d)/first['raw_response']).write_bytes(b'changed')
            with self.assertRaises(ValueError):resolve_name('source',d,resume=True)

    def test_invalid_coordinates_and_service_error(self):
        for body in [b'<html>error</html>',b'{"resolvedCoordinate":[{"ra":999,"decl":2}]}',b'{}']:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(resolve_name('source',d,fetcher=lambda u,t:(200,{},body))['status'],'unavailable')


if __name__ == '__main__':unittest.main()
