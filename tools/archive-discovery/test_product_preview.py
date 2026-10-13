"""Offline validation of bounded receipts and numerical reader semantics."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from product_preview import MAX_PRODUCT_BYTES, read_selected, validate_product_url
from portal_service import Portal

try:
    import numpy as np
    from astropy.io import fits
    from scientific_preview import native_series
    OPTIONAL=True
except ImportError:
    OPTIONAL=False


class Receipts(unittest.TestCase):
    def test_urls_reject_credentials_ports_unknown_hosts_and_non_https(self):
        for url in ('http://dr17.sdss.org/x','https://user:secret@dr17.sdss.org/x','https://localhost/x',
                    'https://dr17.sdss.org:8765/x','https://dr17.sdss.org.evil.invalid/x','file:///tmp/x'):
            with self.assertRaises(ValueError):validate_product_url(url)
        self.assertEqual(validate_product_url('https://dr17.sdss.org/native.fits'),'https://dr17.sdss.org/native.fits')

    def test_oversize_keeps_prefix_but_never_marks_it_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=b'x'*(MAX_PRODUCT_BYTES+1)
            result=read_selected({'product_url':'https://dr17.sdss.org/x'},directory,lambda *args:(200,{},raw,None))
            self.assertEqual(result['status'],'partial')
            self.assertNotIn('native_file',result)
            self.assertEqual(result['source_hash'],hashlib.sha256(raw).hexdigest())
            self.assertEqual((Path(directory)/'native.response').read_bytes(),raw)

    def test_access_failure_and_empty_results_are_not_no_data(self):
        for status,body,expected in ((403,b'denied','access_required'),(500,b'failure','unavailable'),(200,b'','unavailable')):
            with tempfile.TemporaryDirectory() as directory:
                result=read_selected({'product_url':'https://dr17.sdss.org/x'},directory,lambda *args:(status,{},body,None))
                self.assertEqual(result['status'],expected)
                self.assertNotIn('native_file',result)

    def test_known_content_length_budget_keeps_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result=read_selected({'product_url':'https://dr17.sdss.org/x'},directory,
                lambda *args:(200,{'Content-Length':str(MAX_PRODUCT_BYTES+1)},b'','Byte budget; no body read.'))
            self.assertEqual(result['status'],'partial')
            self.assertIn('no body read',result['error'])

    def test_catalogue_zero_retains_value_and_makes_no_download(self):
        with tempfile.TemporaryDirectory() as directory:
            row={'measurements':[{'value':0,'unit':'erg cm^-2 s^-1','error':None}]}
            result=read_selected(row,directory,lambda *args:self.fail('Unexpected download'))
            self.assertEqual(result['mode'],'catalogue_measurements')
            self.assertEqual(json.loads((Path(directory)/'record.json').read_text())['measurements'][0]['value'],0)
            self.assertFalse((Path(directory)/'native.response').exists())



@unittest.skipUnless(OPTIONAL,'Requires the optional portal scientific runtime.')
class NativeReaders(unittest.TestCase):


    def test_missing_generic_units_refuse_plot(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native.fits'
            table=fits.BinTableHDU.from_columns([fits.Column(name='TIME',format='D',array=[1,2]),fits.Column(name='FLUX',format='D',array=[3,4])])
            fits.HDUList([fits.PrimaryHDU(),table]).writeto(path)
            with self.assertRaises(ValueError):native_series(path,{'archive':'mast'})

    def test_native_lightcurve_errors_quality_and_epoch_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native.fits'
            table=fits.BinTableHDU.from_columns([fits.Column(name='TIME',format='D',unit='d',array=[1,2]),
                fits.Column(name='PDCSAP_FLUX',format='D',unit='electron/s',array=[3,4]),
                fits.Column(name='PDCSAP_FLUX_ERR',format='D',unit='electron/s',array=[.1,.2]),
                fits.Column(name='QUALITY',format='J',array=[0,1])])
            table.header['BJDREFI']=2457000;table.header['TIMESYS']='TDB'
            fits.HDUList([fits.PrimaryHDU(),table]).writeto(path)
            row=native_series(path,{'archive':'mast'})[0]
            self.assertEqual(row['time_reference']['BJDREFI'],2457000)
            self.assertEqual(row['y_unit'],'electron/s')
            np.testing.assert_equal(row['error'],[.1,.2]);np.testing.assert_equal(row['mask'],[False,True])

    def test_desi_exact_target_selects_second_row_and_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native.fits'
            hdus=[fits.PrimaryHDU(),fits.BinTableHDU.from_columns([fits.Column(name='TARGETID',format='K',array=[123,456])],name='FIBERMAP')]
            for arm in ('B','R','Z'):
                for suffix,data in (('WAVELENGTH',[4000,4001]),('FLUX',[[1,2],[8,9]]),('IVAR',[[1,1],[4,0]]),('MASK',[[0,0],[0,0]])):
                    hdus.append(fits.ImageHDU(data=np.array(data),name=arm+'_'+suffix))
            fits.HDUList(hdus).writeto(path)
            rows=native_series(path,{'native_metadata':{'targetid':'456'}})
            self.assertEqual(len(rows),3);np.testing.assert_equal(rows[0]['y'],[8,9]);np.testing.assert_equal(rows[0]['mask'],[False,True])
            with self.assertRaises(ValueError):native_series(path,{'native_metadata':{'targetid':'999'}})
