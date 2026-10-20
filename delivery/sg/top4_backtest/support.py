from pathlib import Path
import hashlib
import json
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE_COMMIT = 'def5d53f0f545b99a528b9af72abfe5f1b8f1562'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str,
                                   allow_nan=False), encoding='utf-8', newline='\n')


def add_repo():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))


def check_source_lock():
    lock = json.loads((HERE/'SOURCE_LOCK.json').read_text(encoding='utf-8'))
    for name, expected in lock['canonical_lf_sha256'].items():
        content = (REPO/name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f'Upstream source differs from reviewed legacy Top4: {name}')
    return lock
