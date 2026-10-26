"""Independent dimensional checks for the display-only spectral conversion."""
import copy
import unittest

try:
    import numpy as np
    from scientific_preview import display_series
    OPTIONAL=True
except ImportError:
    OPTIONAL=False


@unittest.skipUnless(OPTIONAL,'Requires the optional scientific runtime.')
class SpectralDensity(unittest.TestCase):
    def source(self,x=(4000.,5000.,6000.),y=(1.,-2.,0.),unit='1e-17 erg cm^-2 s^-1 Angstrom^-1'):
        return dict(x=np.array(x),y=np.array(y),error=np.array([.1,.2,.3]),mask=np.array([False,False,True]),x_unit='vacuum Angstrom',y_unit=unit)

    def test_cgs_jacobian_and_error_scale_independent_of_astropy(self):
        s=self.source();original=copy.deepcopy(s);v=display_series(s)
        # c in angstrom/s; 1 mJy = 1e-26 erg cm^-2 s^-1 Hz^-1.
        factor=1e-17*s['x']**2/2.99792458e18/1e-26
        np.testing.assert_allclose(v['y'],s['y']*factor,rtol=2e-15)
        np.testing.assert_allclose(v['error'],s['error']*factor,rtol=2e-15)
        self.assertLess(v['y'][1],0);self.assertEqual(v['y'][2],0)
        for k in ('x','y','error','mask'):np.testing.assert_equal(s[k],original[k])
        self.assertEqual(v['y'].dtype,np.float64)
        self.assertEqual(v['transform']['wavelength_medium'],'vacuum')

    def test_wavelength_units_and_density_per_nm(self):
        s=self.source(x=(400.,500.,600.),y=(10.,20.,30.),unit='1e-17 erg cm^-2 s^-1 nm^-1');s['x_unit']='nm'
        v=display_series(s)
        np.testing.assert_allclose(v['x'],[4000,5000,6000])
        expected=1e-18*np.array(s['y'])*v['x']**2/2.99792458e18/1e-26
        np.testing.assert_allclose(v['y'],expected)

    def test_flat_fnu_and_native_jy_conversion(self):
        s=self.source(y=(1/16.,1/25.,1/36.));v=display_series(s)
        np.testing.assert_allclose(v['y'],np.repeat(v['y'][0],3))
        s=self.source(unit='Jy');v=display_series(s)
        np.testing.assert_allclose(v['y'],s['y']*1000.)
        np.testing.assert_allclose(v['error'],s['error']*1000.)

    def test_bad_wavelengths_are_display_masked_without_mutating_native_mask(self):
        s=self.source(x=(0.,-5000.,float('nan')));s['mask'][:]=False
        v=display_series(s)
        self.assertTrue(np.all(v['mask']));self.assertTrue(np.all(np.isnan(v['y'])))
        self.assertFalse(np.any(s['mask']))
        self.assertEqual(v['transform']['invalid_wavelength_count'],3)

    def test_no_error_stays_absent_and_mask_is_preserved(self):
        s=self.source();s['error']=None;v=display_series(s)
        self.assertIsNone(v['error']);np.testing.assert_equal(s['mask'],v['mask'])

    def test_counts_relative_flux_surface_brightness_and_band_flux_remain_native(self):
        for unit in ('electron/s','1','erg cm^-2 s^-1','MJy/sr'):
            s=self.source(unit=unit);v=display_series(s)
            self.assertEqual(v['transform']['status'],'native')
            np.testing.assert_equal(s['y'],v['y'])
            self.assertEqual(v['y_unit'],unit)

    def test_time_series_with_physical_flux_retains_time_reference(self):
        s=self.source(unit='Jy');s['x_unit']='d';s['time_reference']={'BJDREFI':2457000}
        v=display_series(s);self.assertEqual(v['transform']['status'],'native')
        np.testing.assert_equal(v['y'],s['y'])


if __name__=='__main__':unittest.main()
