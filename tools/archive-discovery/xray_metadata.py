"""Bounded XMM/Chandra catalogue measurements and separate pointing logs.

Public metadata only. No file download, nearest-source choice, or motion fit.
Responses and receipts are immutable; failed queries are not empty searches.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from urllib.parse import urlencode
from urllib.error import URLError
from xml.etree import ElementTree as ET

from checkpoint_io import MAX_BYTES, fetch, new_attempt_stem, utcnow

HEASARC = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"
CSC = "https://cda.cfa.harvard.edu/csccli/getProperties"
ROUTES = {
    "xmm": (
        dict(id="xmmssc", endpoint=HEASARC, table="xmmssc", release="5XMM-DR15",
             kind="catalogue_source", columns=("srcid", "name", "ra", "dec", "obsid", "n_obs",
             "ep_flux", "ep_flux_error", "ep_1_flux", "ep_1_flux_error", "ep_2_flux",
             "ep_2_flux_error", "ep_3_flux", "ep_3_flux_error", "ep_4_flux", "ep_4_flux_error",
             "ep_5_flux", "ep_5_flux_error", "time", "end_time", "ep_ontime",
             "spectra_flag", "tseries_flag", "sum_flag")),
        dict(id="xmmmaster", endpoint=HEASARC, table="xmmmaster", release="current observation log",
             kind="observation", columns=("obsid", "name", "ra", "dec", "time", "end_time",
             "duration", "status", "public_date", "data_in_heasarc", "pn_time", "mos1_time", "mos2_time"))),
    "chandra": (
        dict(id="csc21", endpoint=CSC, table="master_source", release="CSC 2.1",
             kind="catalogue_source", columns=("name", "ra", "dec", "err_ellipse_r0", "err_ellipse_r1",
             "significance", "conf_flag", "sat_src_flag", "flux_aper_b", "flux_aper_lolim_b",
             "flux_aper_hilim_b", "flux_aper_w", "flux_aper_lolim_w", "flux_aper_hilim_w")),
        dict(id="chanmaster", endpoint=HEASARC, table="chanmaster", release="current observation log",
             kind="observation", columns=("obsid", "name", "ra", "dec", "time", "exposure",
             "detector", "grating", "status", "public_date")))
}
FLUX_UNITS = {"erg/cm^2/s", "erg/s/cm^2", "ergcm-2s-1", "ergs-1cm-2", "erg/(cm2s)"}
WARNINGS = ["Every returned source or pointing is a candidate; no nearest source is selected.",
    "No proper-motion propagation, detector footprint, or source extraction containment is verified.",
    "Catalogue fluxes are archive measurements; no native spectrum or light curve has been read.",
    "Completion applies only to these bounded cones and tables, not all mission data."]


def _number(value, label, required=False):
    if value is None or value == "":
        if required:
            raise ValueError("Missing " + label)
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean " + label)
    try:
        value = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid " + label) from error
    if not math.isfinite(value):
        raise ValueError("Non-finite " + label)
    return value


def _identifier(value, label, required=True):
    if value is None or value == "":
        if required:
            raise ValueError("Missing " + label)
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(label + " must be exact text/integer, never a float")
    value = str(value).strip()
    if not value:
        raise ValueError("Empty " + label)
    return value


def _target(target):
    if not isinstance(target, dict):
        raise ValueError("Target must be a position dictionary")
    ra, dec, radius = [_number(target.get(k), k, True) for k in ("ra_deg", "dec_deg", "radius_arcsec")]
    if not 0 <= ra < 360 or not -90 <= dec <= 90 or not 0 < radius <= 3600:
        raise ValueError("Require RA [0,360), Dec [-90,90], radius (0,3600] arcsec")
    if target.get("frame", "ICRS") != "ICRS":
        raise ValueError("Only ICRS is supported")
    result = dict(target)
    result.update(ra_deg=ra, dec_deg=dec, radius_arcsec=radius, frame="ICRS",
                  position_epoch_jyear=_number(target.get("position_epoch_jyear"), "epoch"),
                  proper_motion_applied=False)
    for key in ("pm_ra_cosdec_mas_per_year", "pm_dec_mas_per_year"):
        if key in result:
            result[key] = _number(result[key], key)
    return result


def query_url(archive, target, route_id, limit=200):
    position = _target(target)
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("Limit must be an integer from 1 to 10000")
    route = next((r for r in ROUTES.get(archive, ()) if r["id"] == route_id), None)
    if route is None:
        raise ValueError("Unsupported X-ray route")
    ra, dec, rad = position["ra_deg"], position["dec_deg"], position["radius_arcsec"]
    if route_id == "csc21":
        # cone_distance is documented for getProperties, not the CSC TAP host.
        # The release is pinned because the default changes with new releases.
        where = f"dbo.cone_distance(m.ra,m.dec,{ra:.15g},{dec:.15g})<={rad/60:.15g}"
    else:
        where = ("CONTAINS(POINT('ICRS',ra,dec),"
                 f"CIRCLE('ICRS',{ra:.15g},{dec:.15g},{rad/3600:.15g}))=1")
    if route_id == "csc21":
        query = f"SELECT TOP {limit+1} " + ",".join("m."+c for c in route["columns"]) + f" FROM {route['table']} m WHERE {where}"
        params = dict(query=query, outputFormat="votable", coordFormat="decimal", floatFormat="native", version="rel2.1")
        return route["endpoint"] + "?" + urlencode(params), params
    query = f"SELECT TOP {limit+1} " + ",".join(route["columns"]) + f" FROM {route['table']} WHERE {where}"
    params = dict(REQUEST="doQuery", LANG="ADQL", FORMAT="votable", MAXREC=limit+1, QUERY=query)
    return route["endpoint"] + "?" + urlencode(params), params


def parse_response(body, required_columns, *, csc_cli=False):
    """Read strict TABLEDATA, BINARY or BINARY2 scalar metadata without Astropy.

    Numeric arrays, external streams and unsupported encodings fail explicitly.
    Integer cells retain Python integer precision; character IDs retain zeroes.
    """
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("DTD/entities are unsupported")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ImportError) as error:
        raise ValueError("VOTable XML unavailable or invalid: " + str(error)) from error
    if root.tag.split("}")[-1] != "VOTABLE":
        raise ValueError("Expected VOTABLE")
    resources = [r for r in root.findall(".//{*}RESOURCE") if r.get("type") == "results" or (csc_cli and r.get("type") is None)]
    if len(resources) != 1:
        raise ValueError("Expected one results resource")
    resource = resources[0]
    statuses = resource.findall("{*}INFO")
    statuses = [s for s in statuses if s.get("name") == "QUERY_STATUS"]
    if (not statuses and not csc_cli) or any(s.get("value") not in ("OK", "OVERFLOW") for s in statuses):
        raise ValueError("TAP query status missing or failed: " + " ".join(s.text or "" for s in statuses))
    if any(i.get("name", "").lower() == "error" or i.get("value") == "ERROR" for i in resource.findall("{*}INFO")):
        raise ValueError("Archive query returned an error")
    tables = resource.findall("{*}TABLE")
    if len(tables) != 1:
        raise ValueError("Expected one result table")
    table = tables[0]
    fields = [dict(f.attrib, null=f.find("{*}VALUES").get("null") if f.find("{*}VALUES") is not None else None)
              for f in table.findall("{*}FIELD")]
    names = [f.get("name") for f in fields]
    if not names or None in names or len(names) != len(set(names)) or not set(required_columns).issubset(names):
        raise ValueError("Missing/duplicate expected fields")
    rows = []
    data = table.find("{*}DATA")
    td = data.find("{*}TABLEDATA") if data is not None else None
    if td is not None:
        for tr in td.findall("{*}TR"):
            cells = tr.findall("{*}TD")
            if len(cells) != len(fields):
                raise ValueError("VOTable row width mismatch")
            row = {}
            for field, cell in zip(fields, cells):
                value = cell.text
                if value is None or value == "" or value == field["null"]:
                    value = None
                elif field.get("datatype") in ("short", "int", "long", "unsignedByte"):
                    value = int(value)
                row[field["name"]] = value
            rows.append(row)
        encoding = "TABLEDATA"
    else:
        nodes = [n for n in list(data or []) if n.tag.split("}")[-1] in ("BINARY", "BINARY2")]
        if len(nodes) != 1:
            raise ValueError("Unsupported VOTable encoding")
        encoding = nodes[0].tag.split("}")[-1]
        stream = nodes[0].find("{*}STREAM")
        if stream is None or stream.get("href") is not None or stream.get("encoding") != "base64":
            raise ValueError("Only inline base64 streams are supported")
        try:
            raw = base64.b64decode("".join((stream.text or "").split()), validate=True)
        except ValueError as error:
            raise ValueError("Invalid base64 stream") from error
        cursor = 0
        def take(size):
            nonlocal cursor
            if size < 0 or size > MAX_BYTES or cursor + size > len(raw):
                raise ValueError("Truncated/invalid binary cell")
            value = raw[cursor:cursor+size]
            cursor += size
            return value
        while cursor < len(raw):
            previous_cursor = cursor
            flags = take((len(fields)+7)//8) if encoding == "BINARY2" else b""
            row = {}
            for index, field in enumerate(fields):
                dtype, shape = field.get("datatype"), field.get("arraysize", "1")
                if not re.fullmatch(r"\d+\*?|\*", shape):
                    raise ValueError("Unsupported binary array shape")
                count = struct.unpack(">i", take(4))[0] if shape.endswith("*") else int(shape)
                if dtype in ("char", "unicodeChar"):
                    value = take(count * (2 if dtype == "unicodeChar" else 1)).decode("utf-16-be" if dtype == "unicodeChar" else "ascii").rstrip("\x00 ")
                else:
                    if count != 1:
                        raise ValueError("Numeric arrays are unsupported")
                    formats = {"short": "h", "int": "i", "long": "q", "unsignedByte": "B", "float": "f", "double": "d"}
                    if dtype not in formats:
                        raise ValueError("Unsupported binary datatype: " + str(dtype))
                    fmt = ">" + formats[dtype]
                    value = struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
                masked = flags and (flags[index//8] & (128 >> (index % 8)))
                if masked or value == "" or (field["null"] is not None and str(value) == field["null"]) or (isinstance(value, float) and math.isnan(value)):
                    value = None
                row[field["name"]] = value
            rows.append(row)
            if cursor == previous_cursor:
                raise ValueError("Binary row has zero byte width")
    if table.get("nrows") is not None and int(table.get("nrows")) != len(rows):
        raise ValueError("Declared VOTable row count mismatch")
    return rows, dict(fields=fields, encoding=encoding, overflow=any(s.get("value") == "OVERFLOW" for s in statuses),
                     service_completion_reported=bool(statuses))


def _separation(a, b, c, d):
    a, b, c, d = map(math.radians, (a, b, c, d))
    h = math.sin((b-d)/2)**2 + math.cos(b)*math.cos(d)*math.sin((a-c)/2)**2
    return math.degrees(2*math.asin(math.sqrt(min(1, max(0, h)))))*3600


def normalise_rows(archive, route_id, rows, fields, target):
    position = _target(target)
    route = next(r for r in ROUTES[archive] if r["id"] == route_id)
    units = {f["name"]: f.get("unit") for f in fields}
    for key in ("ra", "dec"):
        if units.get(key) not in ("deg", "degree", "degrees"):
            raise ValueError("Missing/unexpected coordinate unit: " + key)
    records, seen = [], set()
    for original in rows:
        row = dict(original)
        for key in ("srcid", "obsid"):
            if key in row:
                row[key] = _identifier(row[key], key, key == "srcid" or route["kind"] == "observation")
        name = _identifier(row.get("name"), "name")
        identity = row.get("obsid") if route["kind"] == "observation" else (row.get("srcid") or name)
        identity = route_id + ":" + identity
        if identity in seen:
            raise ValueError("Duplicate record identity within route: " + identity)
        seen.add(identity)
        ra, dec = _number(row.get("ra"), "RA", True), _number(row.get("dec"), "Dec", True)
        if not 0 <= ra < 360 or not -90 <= dec <= 90:
            raise ValueError("Out-of-range archive coordinates")
        sep = _separation(position["ra_deg"], position["dec_deg"], ra, dec)
        record = dict(archive=archive, record_id=identity, record_kind=route["kind"],
            data_release=route["release"], release_branch=route_id, target_name=name,
            facility="XMM-Newton" if archive == "xmm" else "Chandra",
            instrument="EPIC" if route_id == "xmmssc" else row.get("detector"),
            observation_id=row.get("obsid"), product_id=None, product_type="catalogue_measurement" if route["kind"] == "catalogue_source" else "pointing",
            association_status="candidate_not_verified", source_detection_verified=False,
            archive_source_detection=route["kind"] == "catalogue_source", footprint_verified=False,
            native_spectrum_verified=False, native_time_series_verified=False,
            ra_deg=ra, dec_deg=dec, separation_arcsec=sep, within_requested_cone=sep <= position["radius_arcsec"]+1e-6,
            query_position=position, coordinate_epoch_jyear=None,
            match_method="catalogue_position_cone" if route["kind"] == "catalogue_source" else "pointing_centre_cone",
            measurements=[], native_metadata=row, native_units=units,
            product_url=None, product_access_verified=False,
            native_product_links=[], observation_mjd=None, exposure_seconds=None,
            observed_at=None, access_status=row.get("status"), release_date=None)
        for key, output in (("time", "observation_mjd"), ("end_time", "observation_end_mjd"), ("public_date", "release_mjd")):
            if key in row:
                if units.get(key) != "d":
                    raise ValueError("Expected HEASARC MJD day unit: " + key)
                record[output] = _number(row[key], key)
        exposure_key = "duration" if route_id == "xmmmaster" else ("exposure" if route_id == "chanmaster" else "ep_ontime")
        if exposure_key in row:
            if units.get(exposure_key) != "s":
                raise ValueError("Expected seconds: " + exposure_key)
            record["exposure_seconds"] = _number(row[exposure_key], exposure_key)
        if route_id == "xmmssc":
            bands = {"ep_flux": (0.2,12), "ep_1_flux": (0.2,0.5), "ep_2_flux": (0.5,1),
                     "ep_3_flux": (1,2), "ep_4_flux": (2,4.5), "ep_5_flux": (4.5,12)}
            groups = [(key, key+"_error", None, None, band) for key, band in bands.items()]
        elif route_id == "csc21":
            groups = [("flux_aper_"+band, None, "flux_aper_lolim_"+band, "flux_aper_hilim_"+band, energies)
                      for band, energies in (("b", (0.5,7)), ("w", (0.1,10)))]
        else:
            groups = []
        for key, error, lower, upper, band in groups:
            if key not in row:
                raise ValueError("Missing measurement column: " + key)
            for column in (key, error, lower, upper):
                if column and (units.get(column) or "").replace(" ", "") not in FLUX_UNITS:
                    raise ValueError("Missing/unexpected flux unit: " + column)
            values = {label: _number(row.get(column), column) if column else None
                      for label, column in (("value", key), ("error", error), ("lower_bound", lower), ("upper_bound", upper))}
            if values["error"] is not None and values["error"] < 0:
                raise ValueError("Negative flux error")
            if all(values[k] is not None for k in ("lower_bound", "upper_bound")) and values["lower_bound"] > values["upper_bound"]:
                raise ValueError("Inverted flux bounds")
            record["measurements"].append(dict(quantity="energy_flux", native_column=key,
                **values, unit="erg cm^-2 s^-1", native_unit=units[key], band_min=band[0], band_max=band[1],
                band_unit="keV", band_range_approximate=key == "flux_aper_w",
                uncertainty_kind="catalogue_flux_error" if error else "two_sided_confidence_bounds",
                confidence_level=0.68 if lower else None, observation_mjd=record["observation_mjd"],
                time_basis="catalogue_row" if route_id == "xmmssc" else "largest_exposure_Bayesian_block; epoch_not_returned",
                model_dependence="catalogue_energy_conversion_factors" if route_id == "xmmssc" else "archive_aperture_energy_flux"))
        if route["kind"] == "observation" and row.get("status") == "archived" and (archive != "xmm" or row.get("data_in_heasarc") == "Y"):
            obsid = row["obsid"]
            if not obsid.isdigit():
                raise ValueError("Observation ID must contain digits")
            url = ("https://heasarc.gsfc.nasa.gov/FTP/xmm/data/rev0/"+obsid+"/" if archive == "xmm" else
                   "https://heasarc.gsfc.nasa.gov/FTP/chandra/data/byobsid/"+str(int(obsid)%10)+"/"+obsid+"/")
            record["native_product_links"].append(dict(url=url, relation="observation_product_directory",
                product_type="mixed_observation_products", native_file_verified=False))
        if route_id == "xmmssc":
            record.update(spectrum_flag=row.get("spectra_flag"), time_series_flag=row.get("tseries_flag"),
                          catalogue_quality_flag=row.get("sum_flag"),
                          contributing_observations=_number(row.get("n_obs"), "n_obs"))
            if record["contributing_observations"] is not None:
                start = record["observation_mjd"]
                record.update(observation_mjd=None, catalogue_start_mjd=start,
                              catalogue_end_mjd=record.get("observation_end_mjd"),
                              exposure_basis="combined_catalogue_stack")
                for measurement in record["measurements"]:
                    measurement.update(observation_mjd=None, time_start_mjd=start,
                        time_end_mjd=record.get("observation_end_mjd"), time_basis="catalogue_stack_interval")
        records.append(record)
    return records


def _save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def search_xray(archive, target, out, timeout=20, limit=200, fetcher=fetch):
    """Query at most two routes, serially, with no retries or native downloads.

    Access blocks, throttling and network timeouts stop the facility immediately.
    A schema/query error is retained and the independent pointing route may run.
    Each request asks for limit+1. The combined top-level output obeys limit;
    full response bytes and separate branch records remain in the receipt.
    """
    if archive not in ROUTES:
        raise ValueError("Archive must be xmm or chandra")
    position = _target(target)
    timeout = _number(timeout, "timeout", True)
    if not 0 < timeout <= 120:
        raise ValueError("Timeout must be >0 and <=120 seconds")
    requests = [(r, *query_url(archive, position, r["id"], limit)) for r in ROUTES[archive]]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stem = new_attempt_stem(out, archive)
    result = dict(archive=archive, started_at_utc=utcnow(), status="unavailable", records=[],
        warnings=list(WARNINGS), query_complete=False, response_parsed=False, branches=[],
        receipt=stem+".receipt.json", coverage="catalogue source cone and separate pointing-centre cone")
    stop = False
    for route, url, params in requests:
        branch = dict(route_id=route["id"], table=route["table"], data_release=route["release"],
            query_url=url, parameters=params, status="unavailable", query_complete=False,
            response_parsed=False, started_at_utc=utcnow(), records=[])
        if stop:
            branch.update(status="not_queried", error="Facility stopped after access block, throttling, timeout or connection failure")
        else:
            try:
                status, headers, body = fetcher(url, timeout)
                if not isinstance(body, bytes):
                    raise ValueError("Fetcher response must be bytes")
                raw_path = out / (stem+"-"+route["id"]+".response")
                with raw_path.open("xb") as stream:
                    stream.write(body); stream.flush(); os.fsync(stream.fileno())
                branch.update(http_status=status, headers=dict(headers), raw_response=raw_path.name,
                    raw_sha256=hashlib.sha256(body).hexdigest(), response_bytes=len(body))
                if status in (401,403):
                    branch["status"] = "access_required"; stop = True
                elif status != 200:
                    stop = status in (429,502,503,504)
                elif len(body) > MAX_BYTES:
                    branch.update(status="partial", error="Response byte budget exceeded; only received prefix retained")
                    stop = True
                else:
                    rows, metadata = parse_response(body, route["columns"], csc_cli=route["id"] == "csc21")
                    records = normalise_rows(archive, route["id"], rows, metadata["fields"], position)
                    partial = metadata["overflow"] or len(records) > limit
                    branch.update(status="partial" if partial else ("matches" if records else "no_matches"),
                        records=records[:limit], returned_count=len(records), retained_count=min(limit,len(records)),
                        tap_response=metadata, response_parsed=True,
                        query_complete=not partial and metadata["service_completion_reported"])
            except (TimeoutError, OSError, URLError) as error:
                branch["error"] = type(error).__name__+": "+str(error); stop = True
            except (ValueError, TypeError, UnicodeError, struct.error) as error:
                branch["error"] = type(error).__name__+": "+str(error)
        branch["finished_at_utc"] = utcnow()
        result["branches"].append(branch)
        result["records"].extend(branch["records"])
    good = [b for b in result["branches"] if b["response_parsed"]]
    result["response_parsed"] = bool(good)
    result["query_complete"] = all(b["query_complete"] for b in result["branches"])
    if result["query_complete"]:
        result["status"] = "matches" if result["records"] else "no_matches"
    elif good or any(b["status"] == "partial" for b in result["branches"]):
        result["status"] = "partial"
    elif any(b["status"] == "access_required" for b in result["branches"]):
        result["status"] = "access_required"
    if len(result["records"]) > limit:
        result.update(records=result["records"][:limit], query_complete=False, status="partial")
        result["warnings"].append("Combined record budget reached; separate branch records and full response bytes are retained.")
    result.update(retained_count=len(result["records"]), finished_at_utc=utcnow())
    _save_json(out / result["receipt"], result)
    return result
