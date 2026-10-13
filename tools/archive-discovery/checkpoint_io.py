"""Small, shared I/O primitives for metadata queries and durable receipts."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

VERSION = "0.7.0"
MAX_BYTES = 4 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def fetch(url, timeout):
    request = Request(url, headers={
        "Accept": "application/json, application/x-votable+xml, text/xml",
        "User-Agent": "MusingsArchiveDiscovery/" + VERSION})
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read(MAX_BYTES + 1)


def verified_bytes(directory, name, digest):
    """Reject changed receipts and paths outside the checkpoint directory."""
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid saved response path.")
    path = Path(directory) / name
    if path.is_symlink():
        raise ValueError("Saved response must not be a symlink.")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("Saved response checksum differs; preserve this run for inspection.")
    return body


def saved_directory(directory, name):
    """Resolve a child checkpoint without following a redirected directory."""
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid saved checkpoint directory.")
    path = Path(directory) / name
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Saved checkpoint directory is missing or redirected.")
    return path


def new_attempt_stem(directory, prefix):
    """Allocate an unused attempt name while the caller owns the writer lock."""
    base = f"{prefix}-{time.time_ns()}-{uuid.uuid4().hex}"
    directory = Path(directory)
    for counter in range(10000):
        stem = base if counter == 0 else f"{base}-{counter}"
        paths = [directory / (stem + suffix) for suffix in
                 (".response", ".receipt.json", ".receipt.json.tmp")]
        if not any(path.exists() or path.is_symlink() for path in paths):
            return stem
    raise ValueError("Cannot allocate a fresh attempt name; preserve this directory.")
