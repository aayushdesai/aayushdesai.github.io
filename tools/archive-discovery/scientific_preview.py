#!/usr/bin/env python3
"""Provenance-preserving native series and metadata inventory figures.

Run with the optional portal runtime. This file performs no network requests.
"""
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parent/'portal/.plot-cache'))

import numpy as np
from astropy.io import fits
from astropy import units as u

SDSS_MODEL='https://www.sdss4.org/dr17/spectro/spectro_basics/'
DESI_MODEL='https://desidatamodel.readthedocs.io/en/25.3/DESI_SPECTRO_REDUX/SPECPROD/healpix/SURVEY/PROGRAM/PIXGROUP/PIXNUM/coadd-SURVEY-PROGRAM-PIXNUM.html'
FLUX_CONVERSION_REFERENCE='https://docs.astropy.org/en/stable/units/equivalencies.html#spectral-flux-and-luminosity-density-units'
RENDERER_VERSION='0.7.1'


def plotting():
    import matplotlib
    matplotlib.use('Agg')
    for program in ('latex','dvipng','kpsewhich'):
        if not shutil.which(program):
            raise ValueError('Required scientific plotting dependency is missing: '+program)
    if not subprocess.check_output(['kpsewhich','ebgaramond.sty'],text=True).strip():
        raise ValueError('Required EB Garamond LaTeX package is missing.')
    helper=Path(__file__).resolve().parent/'plot_defaults.py'
    if not helper.is_file():
        raise ValueError('The standing plotting defaults helper is missing.')
    spec=importlib.util.spec_from_file_location('aayush_plot_defaults',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import matplotlib.pyplot as plt
    plt.rcParams.update({'axes.spines.top':False,'axes.spines.right':False,'axes.grid':False,'pdf.fonttype':42})
    return plt


def tex(value):
    escapes={'\\':r'\textbackslash{}','_':r'\_','%':r'\%','&':r'\&','#':r'\#','$':r'\$','{':r'\{','}':r'\}','^':r'\textasciicircum{}','~':r'\textasciitilde{}'}
    return ''.join(escapes.get(char,char) for char in str(value))


def native_series(path,record):
    series=[]
    def append(label,x,y,xunit,yunit,error=None,mask=None,reference=None):
        x=np.asarray(x,dtype=float).reshape(-1); y=np.asarray(y,dtype=float).reshape(-1)
        if len(x)!=len(y): raise ValueError('Native coordinate and measurement lengths differ.')
        if not xunit or not yunit: raise ValueError('Native series units are missing; no numerical plot is produced.')
        error=None if error is None else np.asarray(error,dtype=float).reshape(-1)
        mask=np.zeros(len(x),bool) if mask is None else np.asarray(mask,bool).reshape(-1)
        if len(mask)!=len(x) or (error is not None and len(error)!=len(x)): raise ValueError('Native mask/error lengths differ.')
        if error is not None: mask|=~np.isfinite(error)|(error<0)
        mask|=~np.isfinite(x)|~np.isfinite(y)
        series.append(dict(label=label,x=x,y=y,error=error,mask=mask,x_unit=str(xunit),y_unit=str(yunit),reference=reference))
    with fits.open(path,memmap=False) as hdus:
        names={hdu.name.upper():hdu for hdu in hdus}
        if record.get('archive')=='sdssspectra':
            expected=record.get('native_metadata',{}).get('specobjid')
            spec=names.get('SPECOBJ')
            if expected is not None:
                if spec is None or spec.data is None or len(spec.data)!=1 or str(spec.data['SPECOBJID'][0]).strip()!=str(expected):
                    raise ValueError('Saved SDSS spectrum identity does not match native SPECOBJ.')
        if 'FIBERMAP' in names and all(k+'_FLUX' in names for k in ('B','R','Z')):
            selector=record.get('native_metadata',{}).get('targetid')
            if selector is None: raise ValueError('A DESI multi-target file requires an exact saved TARGETID.')
            ids=names['FIBERMAP'].data['TARGETID'];indices=[i for i,value in enumerate(ids) if str(int(value))==str(selector)]
            if len(indices)!=1: raise ValueError('DESI TARGETID did not select exactly one native row.')
            i=indices[0]
            for arm in ('B','R','Z'):
                ivar=np.asarray(names[arm+'_IVAR'].data[i],float);mask=(ivar<=0)|(names[arm+'_MASK'].data[i]!=0)
                error=np.full(len(ivar),np.nan);np.divide(1,ivar,where=ivar>0,out=error);np.sqrt(error,out=error)
                append('DESI '+arm+' arm',names[arm+'_WAVELENGTH'].data,names[arm+'_FLUX'].data[i],
                    'Angstrom','1e-17 erg cm^-2 s^-1 Angstrom^-1',error,mask,DESI_MODEL)
            return series
        for hdu in hdus:
            if not isinstance(hdu,(fits.BinTableHDU,fits.TableHDU)) or hdu.data is None: continue
            cols={column.name.upper():column for column in hdu.columns}
            actual={column.name.upper():column.name for column in hdu.columns}
            if {'LOGLAM','FLUX','IVAR'}<=set(cols) and record.get('archive')=='sdssspectra':
                if hdu.name.upper()!='COADD': continue
                ivar=np.asarray(hdu.data[actual['IVAR']],float).reshape(-1);error=np.full(len(ivar),np.nan)
                np.divide(1,ivar,where=ivar>0,out=error);np.sqrt(error,out=error);mask=ivar<=0
                append('SDSS spectrum',10**np.asarray(hdu.data[actual['LOGLAM']],float),hdu.data[actual['FLUX']],
                    'vacuum Angstrom','1e-17 erg cm^-2 s^-1 Angstrom^-1',error,mask,SDSS_MODEL)
                series[-1]['native_flags']={key.lower():np.asarray(hdu.data[actual[key]]).reshape(-1) for key in ('AND_MASK','OR_MASK') if key in cols}
                series[-1]['mask_policy']='Non-positive IVAR or non-finite values excluded; native AND/OR flags retained for quality review, without interpreting their bit definitions.'
                continue
            coordinate=next((k for k in ('WAVELENGTH','WAVE','LAMBDA','TIME') if k in cols),None)
            value=next((k for k in ('FLUX','PDCSAP_FLUX','SAP_FLUX','RATE') if k in cols),None)
            if not coordinate or not value: continue
            error_key=next((k for k in (value+'_ERROR',value+'_ERR','ERROR','FLUX_ERROR','FLUX_ERR') if k in cols),None)
            unit=cols[coordinate].unit or (hdu.header.get('TIMEUNIT') if coordinate=='TIME' else None)
            yunit=cols[value].unit
            if error_key and cols[error_key].unit not in (None,yunit):
                raise ValueError('Native uncertainty units differ from the plotted value; no implicit conversion is applied.')
            masks=hdu.data[actual['DQ']] if 'DQ' in cols else (hdu.data[actual['QUALITY']] if 'QUALITY' in cols else None)
            x=np.asarray(hdu.data[actual[coordinate]],float);y=np.asarray(hdu.data[actual[value]],float)
            errors=hdu.data[actual[error_key]] if error_key else None
            # Every vector row remains its own series/order; never concatenate orders.
            count=len(x) if x.ndim>1 else 1
            for row in range(count):
                xx=x[row] if count>1 else x;yy=y[row] if count>1 else y
                ee=(np.asarray(errors)[row] if count>1 else errors) if errors is not None else None
                mm=(np.asarray(masks)[row] if count>1 else masks) if masks is not None else None
                label=(record.get('facility') or record.get('archive','Archive').upper())+(' light curve' if coordinate=='TIME' else ' spectrum')+(' component '+str(row+1) if count>1 else '')
                append(label,xx,yy,unit,yunit,ee,
                    None if mm is None else np.asarray(mm)!=0)
                series[-1]['native_hdu']=hdu.name
                series[-1]['mask_policy']='Nonzero native DQ/QUALITY flags or non-finite values excluded; no correction or rebinning is applied.'
                if mm is not None:series[-1]['native_flags']={'dq' if 'DQ' in cols else 'quality':np.asarray(mm).reshape(-1)}
                if coordinate=='TIME':
                    refs={k:hdus[0].header.get(k,hdu.header.get(k)) for k in ('TIMESYS','TIMEREF','BJDREFI','BJDREFF','MJDREFI','MJDREFF','TIMEZERO')}
                    series[-1]['time_reference']=refs
    if not series: raise ValueError('No supported native numerical series with declared units was found. Images/events require a different reader; no flux is inferred.')
    if len(series)>24: raise ValueError('Native file contains more than 24 series; retain the product and use the archive viewer.')
    return series


def save_figure(fig,out,stem):
    plots={}
    for extension in ('svg','png','pdf'):
        name=stem+'.'+extension;fig.savefig(out/name,bbox_inches='tight');plots[extension]=name
    return plots


def display_series(series):
    """Return separate float64 display arrays; never modify native arrays/flags.

    Only wavelength-based energy-flux densities are convertible. Counts, relative
    fluxes, surface brightness and integrated band fluxes need other information.
    The Jacobian scales each supplied standard error by the same positive factor;
    it does not add calibration, wavelength or covariance information.
    """
    view={key:np.array(series[key],dtype=np.float64 if key!='mask' else bool,copy=True)
          if series[key] is not None else None for key in ('x','y','error','mask')}
    view.update(x_unit=series['x_unit'],y_unit=series['y_unit'],
                transform=dict(status='native',reason='No supported wavelength flux-density conversion.'))
    if 'time_reference' in series:
        view['transform']['reason']='Time series retains its native units and time reference.'
        return view
    try:
        xunit=u.Unit(series['x_unit'].removeprefix('vacuum '))
        yunit=u.Unit(series['y_unit'])
    except (ValueError,TypeError):
        view['transform']['reason']='Native units cannot be interpreted safely; no F_nu conversion.'
        return view
    if not xunit.is_equivalent(u.AA):return view
    flam=u.erg/u.cm**2/u.s/u.AA
    if not (yunit.is_equivalent(flam) or yunit.is_equivalent(u.Jy)):
        view['transform']['reason']='Wavelength data have no convertible energy-flux-density units; native values retained.'
        return view
    wavelength=np.asarray(xunit.to(u.AA,view['x']),dtype=np.float64)
    valid=np.isfinite(wavelength)&(wavelength>0)
    safe=np.where(valid,wavelength,1.)*u.AA
    factor=np.broadcast_to(yunit.to(u.mJy,equivalencies=u.spectral_density(safe)),wavelength.shape)
    factor=np.where(valid,factor,np.nan)
    with np.errstate(over='ignore',invalid='ignore'):
        view['x']=wavelength
        view['y']=view['y']*factor
        if view['error'] is not None:view['error']=view['error']*np.abs(factor)
    view['mask']|=~valid|~np.isfinite(view['y'])
    if view['error'] is not None:view['mask']|=~np.isfinite(view['error'])
    view.update(x_unit='angstrom',y_unit='mJy',transform=dict(status='fnu',
        formula='F_nu = F_lambda * wavelength^2 / c, with explicit unit conversion' if yunit.is_equivalent(flam) else 'Native F_nu converted to mJy',
        source_x_unit=series['x_unit'],source_y_unit=series['y_unit'],
        uncertainty='Supplied per-sample errors multiplied by the same absolute conversion factor; no extra uncertainty inferred.',
        wavelength_medium='vacuum' if series['x_unit'].startswith('vacuum ') else 'native convention retained',
        reference=FLUX_CONVERSION_REFERENCE,invalid_wavelength_count=int(np.sum(~valid)),
        rebinning=False,redshift_correction=False))
    return view


def product(source,out):
    source=Path(source);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    record=json.loads((source/'record.json').read_text());receipt=json.loads((source/'receipt.json').read_text())
    if receipt['status']!='ready': raise ValueError('Native receipt is not ready for scientific plotting.')
    plt=plotting();summaries=[];plots=[]
    if record.get('measurements'):
        groups={}
        for measurement in record['measurements']:
            if measurement.get('value') is not None: groups.setdefault(measurement['unit'],[]).append(measurement)
        if not groups: raise ValueError('Catalogue record has no numerical values.')
        for index,(unit,rows) in enumerate(groups.items()):
            fig,ax=plt.subplots(figsize=(7.3,4.3),layout='constrained');x=np.arange(len(rows));y=[r['value'] for r in rows]
            for i,r in enumerate(rows):
                error=r.get('error')
                lo,hi=r.get('lower_bound'),r.get('upper_bound')
                bounds=[[r['value']-lo],[hi-r['value']]] if lo is not None and hi is not None and lo<=r['value']<=hi else None
                ax.errorbar(i,r['value'],yerr=error if error is not None else bounds,fmt='o',color='#005f73',capsize=3)
            labels=[f"{r['band_min']:g}--{r['band_max']:g} {r.get('band_unit','')}" for r in rows]
            ax.set_xticks(x,[tex(label) for label in labels],rotation=30,ha='right');ax.set_ylabel(r'Energy flux ($\mathrm{erg}\,\mathrm{cm}^{-2}\,\mathrm{s}^{-1}$)' if unit=='erg cm^-2 s^-1' else tex(unit));ax.set_xlabel('Archive energy band')
            ax.ticklabel_format(axis='y',style='sci',scilimits=(-2,2));plots.append(save_figure(fig,out,'measurements-'+str(index)));plt.close(fig)
            summaries.append(dict(label='Catalogue energy flux',point_count=len(rows),error_count=sum(r.get('error') is not None or r.get('lower_bound') is not None for r in rows),masked_count=0,x_unit='energy bands; keV',y_unit=unit))
        with (out/'measurements.json').open('x') as stream: json.dump(record['measurements'],stream,indent=2,allow_nan=False)
    else:
        raw=source/receipt['native_file']
        if hashlib.sha256(raw.read_bytes()).hexdigest()!=receipt['source_hash']: raise ValueError('Native source hash changed.')
        for index,series in enumerate(native_series(raw,record)):
            view=display_series(series)
            x,y,error,mask=(view[k] for k in ('x','y','error','mask'));good=~mask
            if not np.any(good): raise ValueError('All native points are masked or non-finite.')
            fig,ax=plt.subplots(figsize=(7.3,4.3),layout='constrained')
            if error is not None:
                if 'time_reference' in series:
                    ax.errorbar(x[good],y[good],yerr=error[good],fmt='none',ecolor='#005f73',alpha=.16,lw=.4,zorder=1)
                else:
                    ax.fill_between(x,np.where(good,y-error,np.nan),np.where(good,y+error,np.nan),color='#005f73',alpha=.12,lw=0,zorder=1)
            if 'time_reference' in series:
                ax.scatter(x[good],y[good],s=5,color='#005f73',zorder=3)
            else:
                ax.plot(x,np.where(good,y,np.nan),lw=1.1,color='#005f73',zorder=3)
            finite=mask&np.isfinite(x)&np.isfinite(y)
            ax.scatter(x[finite],y[finite],s=3,color='.5',alpha=.25,zorder=0,label='Excluded points')
            x_label='Wavelength (angstrom)' if 'angstrom' in view['x_unit'].lower() else tex(view['x_unit'])
            if 'time_reference' in series:
                x_label='Time ('+tex(series['x_unit'])+')'
            ax.set_xlabel(x_label)
            y_label=r'Flux density ($10^{-17}\,\mathrm{erg}\,\mathrm{cm}^{-2}\,\mathrm{s}^{-1}\,\mathrm{\AA}^{-1}$)' if series['y_unit']=='1e-17 erg cm^-2 s^-1 Angstrom^-1' else tex(series['y_unit'])
            if view['transform']['status']=='fnu':y_label=r'$F_\nu$ (mJy)'
            if series['y_unit'] in ('e-/s','electron/s','electrons/s'):y_label=r'Flux ($\mathrm{electron}\,\mathrm{s}^{-1}$)'
            ax.set_ylabel(y_label)
            if np.any(finite):ax.legend(frameon=False,fontsize=11)
            plots.append(save_figure(fig,out,'series-'+str(index)));plt.close(fig)
            summary={k:v for k,v in series.items() if k not in ('x','y','error','mask','native_flags')}
            summary.update(point_count=len(x),error_count=int(np.sum(good)) if error is not None else 0,masked_count=int(np.sum(mask)))
            summary.update(display_x_unit=view['x_unit'],display_y_unit=view['y_unit'],
                           display_transform=view['transform'],native_masked_count=int(np.sum(series['mask'])))
            flags=series.get('native_flags',{})
            summary['native_flagged_counts']={key:int(np.sum(value!=0)) for key,value in flags.items()}
            summaries.append(summary)
            with (out/('series-'+str(index)+'.csv')).open('x',newline='') as stream:
                derived=view['transform']['status']=='fnu'
                writer=csv.writer(stream);writer.writerow(['native_x','native_y','native_error','native_masked']+list(flags)+(['wavelength_angstrom','fnu_mjy','fnu_error_mjy','display_masked'] if derived else []))
                for i in range(len(x)):
                    row=[series['x'][i],series['y'][i],'' if series['error'] is None else series['error'][i],bool(series['mask'][i])]+[flags[key][i] for key in flags]
                    if derived:row += [x[i],y[i],'' if error is None else error[i],bool(mask[i])]
                    writer.writerow(row)
    warnings=list(receipt['warnings'])
    if any(s.get('display_transform',{}).get('status')=='fnu' for s in summaries):
        warnings += ['Supported wavelength flux densities are displayed as F_nu in mJy; native arrays, units and flags remain in the CSV alongside derived columns. Only units change; no resampling, extinction or redshift correction is applied.',
                     'The uncertainty band uses the supplied per-sample errors. Calibration, wavelength and covariance uncertainty are not inferred.']
    if record.get('measurements'):
        warnings += ['Selected catalogue source is a candidate at its own coordinates; these values are not all measurements of the requested target.',
            'The total energy band overlaps its sub-bands; they are separate archive measurements, not independent components to sum.',
            'Catalogue error confidence is unknown unless a confidence level is supplied; no upper limit is inferred from a small value.',
            'Time basis: '+', '.join(sorted(set(str(m.get('time_basis')) for m in record['measurements']))),
            'Archive catalogue flux conversion is model dependent; native model flags remain in the source record.']
    elif record.get('archive')=='sdssspectra':
        warnings += ['Native SDSS wavelengths are heliocentric vacuum values where the SDSS reader applies.',
            'Native flags remain in the CSV; only non-positive inverse variance and non-finite points are excluded by the SDSS reader.']
    else:
        warnings += ['Native units, time references and QUALITY/DQ values are retained; no barycentric correction is applied again.']
        if record.get('native_vs_document_position_separation_arcsec') is not None:
            warnings += ['Native and source-document coordinates differ by '+str(record['native_vs_document_position_separation_arcsec'])+' arcsec; source association remains unverified.']
    result=dict(receipt,status='ready',scientific_renderer_version=RENDERER_VERSION,series=summaries,plots=plots,warnings=warnings,
        catalogue_time_basis=[m.get('time_basis') for m in record.get('measurements',[])],
        downloads=[p.name for p in out.iterdir() if p.suffix in ('.csv','.json') and p.name!='preview.json']+['preview.json'])
    (out/'preview.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


def record_date(record):
    native=record.get('native_metadata',{})
    for value in (record.get('observation_mjd'),record.get('catalogue_start_mjd'),native.get('t_min')):
        try:
            if value is not None and np.isfinite(float(value)): return float(value)
        except (TypeError,ValueError): pass
    for value in (record.get('observed_at'),record.get('observation_date'),native.get('date_obs'),native.get('ut_datetime')):
        if not value:continue
        try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).replace(tzinfo=timezone.utc).timestamp()/86400+40587
        except ValueError:pass
    return None


def overview(source,out):
    report=json.loads(Path(source).read_text());out=Path(out);out.mkdir(parents=True,exist_ok=True);plt=plotting()
    archives=report.get('archives') or {report.get('archive','mast'):report}
    records=[r for result in archives.values() for r in result.get('records',[])];groups={};types={};dated=0
    for record in records:
        native=record.get('native_metadata',{})
        kind=str(record.get('product_type') or native.get('dataproduct_type') or record.get('record_kind') or 'metadata')
        kind={'catalogue_measurement':'Catalogue flux measurements','catalogue_source':'Catalogue source candidates',
            'catalogue_target':'Catalogue targets','observation_product':'Native products','pointing':'Pointing records',
            'spectrum':'Spectra','timeseries':'Time series','image':'Images','cube':'Native cubes','observation':'Observations'}.get(kind,kind.replace('_',' '))
        label='/'.join(str(v) for v in (record.get('archive'),record.get('instrument'),kind) if v)
        groups.setdefault(label,[]).append(record_date(record))
        types[kind]=types.get(kind,0)+1
    height=max(2.6,min(12,1.7+.35*len(groups)))
    fig,(ax,bars)=plt.subplots(2,1,figsize=(7.5,height+2.2),gridspec_kw={'height_ratios':[height,2]},layout='constrained')
    for i,(label,dates) in enumerate(groups.items()):
        values=[value for value in dates if value is not None];dated+=len(values)
        ax.scatter(values,[i]*len(values),s=15,color='#005f73',alpha=.6)
    ax.set_yticks(range(len(groups)),[tex(label[:55]) for label in groups]);ax.set_xlabel('Native observation/catalogue interval start (MJD)')
    ax.set_ylim(max(len(groups)-.6,.6),-.6)
    ax.set_title('Returned target data inventory',fontsize=16)
    if not dated: ax.text(.5,.5,'No native dates in returned records',transform=ax.transAxes,ha='center')
    kinds=list(types);bars.barh(range(len(kinds)),[types[k] for k in kinds],color='#005f73');bars.set_yticks(range(len(kinds)),[tex(k[:48]) for k in kinds]);bars.invert_yaxis();bars.set_xlabel('Returned records (products may repeat or bundle sources)')
    state_parts=[name+': '+value.get('status','unknown') for name,value in archives.items()]
    states='\n'.join('; '.join(state_parts[i:i+3]) for i in range(0,len(state_parts),3))
    fig.suptitle(tex(states),fontsize=11,wrap=True)
    plots=save_figure(fig,out,'overview');plt.close(fig)
    result=dict(plots=plots,returned_records=len(records),dated_records=dated,undated_records=len(records)-dated,
        source_hash=hashlib.sha256(Path(source).read_bytes()).hexdigest(),archive_statuses={k:v.get('status') for k,v in archives.items()},
        limitation='Metadata inventory only. Returned candidates are not verified target detections; partial/unavailable/unsupported sources and undated holdings remain gaps. No flux is inferred.')
    (out/'overview.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1] not in ('overview','product'):raise SystemExit('usage: scientific_preview.py overview|product SOURCE OUTPUT')
    globals()[sys.argv[1]](sys.argv[2],sys.argv[3])
