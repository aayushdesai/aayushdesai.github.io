import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import archive_discovery as archive
from tap_metadata import parse_votable

SAMPLE = b'''<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
<RESOURCE type="results"><INFO name="QUERY_STATUS" value="OK"/>
<TABLE><FIELD name="obs_publisher_did" datatype="char" arraysize="*"/>
<FIELD name="s_ra" datatype="double" unit="deg"/>
<FIELD name="s_dec" datatype="double" unit="deg"/>
<FIELD name="t_min" datatype="double" unit="d"/>
<DATA><TABLEDATA><TR><TD>ivo://eso.org/ID</TD><TD>0.001</TD><TD>0</TD><TD/></TR>
</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>'''


class TapTests(unittest.TestCase):
    def test_units_and_nulls_preserved(self):
        rows, meta = parse_votable(SAMPLE)
        self.assertIsNone(rows[0]['t_min'])
        self.assertEqual(meta['fields'][1]['unit'], 'deg')
        record = archive.normalise('eso', rows, archive.validate_target(0, 0, 5))[0]
        self.assertAlmostEqual(record['separation_arcsec'], 3.6)
        self.assertIsNone(record['observation_mjd'])
        self.assertFalse(record['footprint_verified'])

    def test_error_and_missing_status_are_not_empty_matches(self):
        cases = [SAMPLE.replace(b'value="OK"', b'value="ERROR"'),
                 SAMPLE.replace(b'<INFO name="QUERY_STATUS" value="OK"/>', b''),
                 SAMPLE.replace(b'<TD>0.001</TD>', b''),
                 SAMPLE.replace(b'<TABLEDATA>', b'<BINARY>').replace(b'</TABLEDATA>', b'</BINARY>'),
                 b'<!DOCTYPE VOTABLE>' + SAMPLE, b'<html>server error</html>']
        for body in cases:
            with self.subTest(body=body[:60]), self.assertRaises(ValueError):
                parse_votable(body)

    def test_overflow_after_table_is_partial(self):
        body = SAMPLE.replace(b'</RESOURCE>', b'<INFO name="QUERY_STATUS" value="OVERFLOW"/></RESOURCE>')
        with tempfile.TemporaryDirectory() as directory:
            result = archive.search_archive('eso', archive.validate_target(0,0,5),
                Path(directory), 1, 500, lambda u,t:(200, {}, body))
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['retained_count'], 1)
            self.assertTrue(result['tap_response']['overflow'])

    def test_empty_table_and_schema_drift(self):
        body = SAMPLE.replace(b'<TR><TD>ivo://eso.org/ID</TD><TD>0.001</TD><TD>0</TD><TD/></TR>',b'')
        rows, _ = parse_votable(body, ('obs_publisher_did',))
        self.assertEqual(rows, [])
        with self.assertRaises(ValueError):
            parse_votable(body, ('missing_column',))

    def test_query_radius_is_degrees_and_extra_row_checks_limit(self):
        url = archive.query_url('eso', archive.validate_target(1,2,36),limit=20)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['MAXREC'], ['21'])
        self.assertIn('TOP 21',query['QUERY'][0])
        self.assertIn("CIRCLE('ICRS',1,2,0.01)",query['QUERY'][0])


if __name__ == '__main__':
    unittest.main()
