#!/usr/bin/env python3
"""Offline archive-coverage registry. Listing or validation sends no requests."""
import argparse
import ast
import copy
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

REGISTRY_PATH = Path(__file__).with_suffix('.json')
KINDS = ('spectrum', 'photometry', 'image', 'time_series')
STATES = ('implemented', 'documented', 'blocked', 'deferred')
SERVICE_LEVELS = {'none', 'native_spectra', 'catalogue_photometry', 'time_series',
                  'images', 'observation_products', 'target_metadata'}
LOCAL_LEVELS = {'none', 'catalogue_candidates', 'observation_candidates', 'product_metadata', 'native_measurements'}
LIMIT_KEYS = {'position', 'epoch', 'proper_motion', 'footprint', 'access',
              'completeness', 'product_links'}
EVIDENCE_KINDS = {'offline_tests', 'live_response', 'live_schema', 'failed_live', 'limited_handoff'}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _local_path(value):
    path = Path(value)
    return _nonempty(value) and not path.is_absolute() and '..' not in path.parts


def validate_registry(registry, check_bindings=False, root=None):
    """Return schema/evidence errors; never import or invoke an archive adapter.

    ``check_bindings`` also checks evidence-file existence and top-level adapter
    functions using the local Python syntax tree. It does not verify service health.
    """
    errors = []
    if not isinstance(registry, dict):
        return ['registry must be an object']
    if type(registry.get('schema_version')) is not int or registry['schema_version'] != 1:
        errors.append('schema_version must be 1')
    for key in ('version', 'checked_on', 'scope', 'association_policy'):
        if not _nonempty(registry.get(key)):
            errors.append(key + ' must be nonempty text')
    if not isinstance(registry.get('sources'), list) or not registry['sources']:
        return errors + ['sources must be a nonempty list']
    root = Path(root) if root is not None else REGISTRY_PATH.parent
    seen = set()
    for index, source in enumerate(registry['sources']):
        prefix = 'sources[' + str(index) + ']'
        if not isinstance(source, dict):
            errors.append(prefix + ' must be an object')
            continue
        identifier = source.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identifier):
            errors.append(prefix + '.id must be a lowercase slug')
        elif identifier in seen:
            errors.append(prefix + '.id is duplicated')
        else:
            seen.add(identifier)
        for key in ('archive', 'label', 'release', 'route', 'next_step'):
            if not _nonempty(source.get(key)):
                errors.append(prefix + '.' + key + ' must be nonempty text')
        state = source.get('route_state')
        if state not in STATES:
            errors.append(prefix + '.route_state is invalid')
        for key in ('bands', 'identifiers', 'record_kinds', 'advertised_record_kinds'):
            value = source.get(key)
            if not isinstance(value, list) or any(not _nonempty(x) for x in value):
                errors.append(prefix + '.' + key + ' must be a list of text')
            elif len(set(value)) != len(value):
                errors.append(prefix + '.' + key + ' contains duplicates')
        capabilities = source.get('capabilities', {})
        if not isinstance(capabilities, dict) or set(capabilities) != set(KINDS):
            errors.append(prefix + '.capabilities must contain all four data kinds')
            capabilities = {}
        for kind, capability in capabilities.items():
            if not isinstance(capability, dict) or set(capability) != {'service', 'local'}:
                errors.append(prefix + '.capabilities.' + kind + ' needs service and local')
                continue
            if (not isinstance(capability['service'], str) or capability['service'] not in SERVICE_LEVELS or
                    not isinstance(capability['local'], str) or capability['local'] not in LOCAL_LEVELS):
                errors.append(prefix + '.capabilities.' + kind + ' has an invalid level')
            if state != 'implemented' and capability['local'] != 'none':
                errors.append(prefix + ' cannot claim local capability without an implemented route')
            if capability['service'] == 'none' and capability['local'] != 'none':
                errors.append(prefix + ' cannot claim local capability for an absent service kind')
        limits = source.get('limits')
        if not isinstance(limits, dict) or set(limits) != LIMIT_KEYS or any(not _nonempty(x) for x in limits.values()):
            errors.append(prefix + '.limits must describe all required limits')
        validation = source.get('validation')
        if (not isinstance(validation, dict) or set(validation) != {'offline', 'live'} or
                any(type(x) is not bool for x in validation.values())):
            errors.append(prefix + '.validation needs offline/live booleans')
        callables = source.get('callables')
        if not isinstance(callables, list):
            errors.append(prefix + '.callables must be a list')
            callables = []
        if state == 'implemented' and not callables or state not in ('implemented', 'blocked') and callables:
            errors.append(prefix + ' implemented state and callable bindings disagree')
        for binding in callables:
            if not isinstance(binding, dict):
                errors.append(prefix + ' callable must be an object')
                continue
            if state == 'blocked' and binding.get('operation') != 'blocked_status':
                errors.append(prefix + ' blocked routes may only bind an explicit blocked-status handler')
            module, function = binding.get('module'), binding.get('function')
            if (not isinstance(module, str) or not re.fullmatch(r'[a-z_][a-z0-9_]*', module) or
                    not isinstance(function, str) or not re.fullmatch(r'[a-z_][a-z0-9_]*', function) or
                    not _nonempty(binding.get('operation')) or not isinstance(binding.get('arguments'), dict)):
                errors.append(prefix + ' callable has invalid module/function/operation/arguments')
                continue
            if check_bindings:
                try:
                    tree = ast.parse((root / (module + '.py')).read_text(encoding='utf-8'))
                    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function for node in tree.body):
                        errors.append(prefix + ' callable function is absent: ' + module + '.' + function)
                except (OSError, SyntaxError) as exc:
                    errors.append(prefix + ' callable source cannot be checked: ' + str(exc))
        references = source.get('references')
        if not isinstance(references, list) or not references:
            errors.append(prefix + '.references must include primary documentation')
        else:
            for reference in references:
                if not isinstance(reference, dict) or any(not _nonempty(reference.get(key)) for key in ('title', 'url', 'supports')):
                    errors.append(prefix + ' reference needs title/url/supports')
                elif urlsplit(reference['url']).scheme not in ('https', 'http') or not urlsplit(reference['url']).netloc:
                    errors.append(prefix + ' reference URL is invalid')
        evidence = source.get('evidence')
        if not isinstance(evidence, list):
            errors.append(prefix + '.evidence must be a list')
            evidence = []
        evidence_kinds = set()
        for item in evidence:
            if (not isinstance(item, dict) or not isinstance(item.get('kind'), str) or
                    item['kind'] not in EVIDENCE_KINDS or not _nonempty(item.get('claim')) or
                    not _nonempty(item.get('path')) or not _local_path(item['path'])):
                errors.append(prefix + ' evidence needs kind, bounded relative path, and claim')
                continue
            evidence_kinds.add(item['kind'])
            if check_bindings and not (root / item['path']).is_file():
                errors.append(prefix + ' evidence file is absent: ' + item['path'])
        if isinstance(validation, dict):
            if validation.get('offline') and 'offline_tests' not in evidence_kinds:
                errors.append(prefix + ' offline validation lacks test evidence')
            if validation.get('live') and 'live_response' not in evidence_kinds:
                errors.append(prefix + ' live validation lacks successful response evidence')
        if state == 'implemented' and not source.get('record_kinds'):
            errors.append(prefix + ' implemented route needs emitted record kinds')
    return errors


def load_registry(path=None, check_bindings=False):
    """Read and validate the registry; return a fresh ordinary dictionary."""
    path = Path(path) if path is not None else REGISTRY_PATH
    registry = json.loads(path.read_text(encoding='utf-8'))
    errors = validate_registry(registry, check_bindings=check_bindings, root=path.parent)
    if errors:
        raise ValueError('Invalid coverage registry: ' + '; '.join(errors))
    return registry


def select_sources(registry=None, kind=None, state=None, callable_only=False,
                   band=None, local_only=False, include_deferred=True):
    """Filter routes, preserving registry order; returned entries are independent copies.

    By default a data-kind filter describes documented service holdings. Set
    ``local_only`` to require an implemented local metadata capability. Neither
    option means that the queried source has a verified detection or spectrum.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError('Unknown data kind: ' + str(kind))
    if state is not None and state not in STATES:
        raise ValueError('Unknown route state: ' + str(state))
    registry = load_registry() if registry is None else registry
    errors = validate_registry(registry)
    if errors:
        raise ValueError('Invalid coverage registry: ' + '; '.join(errors))
    selected = []
    for source in registry['sources']:
        if state is not None and source['route_state'] != state:
            continue
        if not include_deferred and source['route_state'] == 'deferred':
            continue
        if callable_only and not source['callables']:
            continue
        if kind is not None and source['capabilities'][kind]['local' if local_only else 'service'] == 'none':
            continue
        if kind is None and local_only and not any(x['local'] != 'none' for x in source['capabilities'].values()):
            continue
        if band is not None and band.casefold() not in {x.casefold() for x in source['bands']}:
            continue
        selected.append(copy.deepcopy(source))
    return selected


def get_source(identifier, registry=None):
    for source in select_sources(registry):
        if source['id'] == identifier:
            return source
    raise KeyError(identifier)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--kind', choices=KINDS)
    parser.add_argument('--state', choices=STATES)
    parser.add_argument('--callable-only', action='store_true')
    parser.add_argument('--local-only', action='store_true')
    parser.add_argument('--band')
    parser.add_argument('--exclude-deferred', action='store_true')
    parser.add_argument('--json', action='store_true', help='Print selected full registry entries.')
    parser.add_argument('--validate', action='store_true', help='Check schema, bindings and evidence files without querying services.')
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry, check_bindings=args.validate)
        sources = select_sources(registry, args.kind, args.state, args.callable_only,
                                 args.band, args.local_only, not args.exclude_deferred)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    if args.json:
        print(json.dumps(dict(schema_version=1, sources=sources), indent=2, allow_nan=False))
    else:
        if args.validate:
            print('Registry schema, callable bindings and evidence paths verified offline.')
        for source in sources:
            kinds = ', '.join(key for key in KINDS if source['capabilities'][key]['service'] != 'none')
            callable_text = 'callable' if source['callables'] else 'no local adapter'
            print(f"{source['id']}: {source['route_state']}; {callable_text}; {kinds}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
