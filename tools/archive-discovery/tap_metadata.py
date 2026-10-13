"""Small, strict reader for TAP VOTable TABLEDATA metadata responses."""
from xml.etree import ElementTree as ET


def parse_votable(body, required_columns=()):
    # This client only needs plain table metadata, never DTDs or entities.
    upper = body.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD/entity declarations are unsupported.")
    try:
        root = ET.fromstring(body)
    except ImportError as exc:
        raise ValueError("Python XML parser is unavailable; use a Python installation with working expat.") from exc
    except ET.ParseError as exc:
        raise ValueError("Invalid VOTable XML: " + str(exc)) from exc
    if root.tag.split('}')[-1] != 'VOTABLE':
        raise ValueError("Expected a VOTABLE response.")
    resources = [r for r in root.findall('.//{*}RESOURCE') if r.get('type') == 'results']
    if len(resources) != 1:
        raise ValueError("Expected exactly one TAP results resource.")
    resource = resources[0]
    statuses = [i for i in resource.findall('{*}INFO') if i.get('name') == 'QUERY_STATUS']
    if not statuses:
        raise ValueError("TAP response lacks QUERY_STATUS.")
    for info in statuses:
        if info.get('value') not in ('OK', 'OVERFLOW'):
            raise ValueError("TAP query failed: " + (info.text or info.get('value') or 'unknown'))
    tables = resource.findall('{*}TABLE')
    if len(tables) != 1:
        raise ValueError("Expected exactly one result table.")
    table = tables[0]
    fields = [dict(f.attrib) for f in table.findall('{*}FIELD')]
    names = [f.get('name') for f in fields]
    if not names or any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("Missing or duplicate VOTable field names.")
    if not set(required_columns).issubset(names):
        raise ValueError("TAP response lacks expected columns.")
    data = table.find('{*}DATA/{*}TABLEDATA')
    if data is None:
        raise ValueError("Only TABLEDATA encoding is supported; response retained for inspection.")
    rows = []
    for tr in data.findall('{*}TR'):
        cells = tr.findall('{*}TD')
        if len(cells) != len(names):
            raise ValueError("VOTable row width differs from declared fields.")
        rows.append(dict(zip(names, [cell.text if cell.text not in (None, '') else None for cell in cells])))
    return rows, dict(fields=fields, overflow=any(i.get('value') == 'OVERFLOW' for i in statuses))
