"""Durable stage snapshots. Reads never open a mutable DataStore on a live run."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .agents.Obtainer.datamixer.cas import ContentStore

STAGES = {'raw': '原始网页', 'corpus': '可用正文与证据',
          'source-review': '正文筛选记录', 'candidates': 'QA 候选及审核记录'}


def save_stage(root, stage, key, record):
    path = Path(root) / 'qa_stages.sqlite'
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS artifacts (stage TEXT, key TEXT, record TEXT, PRIMARY KEY(stage,key))')
        db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?)',
                   (stage, key, json.dumps(record, ensure_ascii=False)))
        db.commit()


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def stage_records(root, stage):
    root = Path(root)
    if stage in {'raw', 'corpus'}:
        path = root / 'catalog.db'
        if not path.exists():
            return
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            cas = ContentStore(root)
            # A read transaction gives a consistent snapshot while workers keep writing.
            db.execute('BEGIN')
            for r in db.execute('SELECT sample_id,cid,tags_json,created_at FROM samples WHERE quality_level=? ORDER BY created_at,sample_id',
                                ('L1' if stage == 'raw' else 'L2',)):
                yield {'sample_id': r['sample_id'], 'created_at': r['created_at'],
                       'content': cas.get_json(r['cid']), 'metadata': json.loads(r['tags_json'] or '{}')}
    else:
        path = root / 'qa_stages.sqlite'
        if not path.exists():
            return
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            for row in db.execute('SELECT record FROM artifacts WHERE stage=? ORDER BY key', (stage,)):
                yield json.loads(row[0])


def stage_counts(root):
    root = Path(root)
    counts = dict.fromkeys(STAGES, 0)
    try:
        if (root / 'catalog.db').exists():
            with closing(sqlite3.connect((root / 'catalog.db').resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                for level, count in db.execute("SELECT quality_level,COUNT(*) FROM samples WHERE quality_level IN ('L1','L2') GROUP BY quality_level"):
                    counts['raw' if level == 'L1' else 'corpus'] = count
        if (root / 'qa_stages.sqlite').exists():
            with closing(sqlite3.connect((root / 'qa_stages.sqlite').resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                for stage, count in db.execute('SELECT stage,COUNT(*) FROM artifacts GROUP BY stage'):
                    if stage in counts:
                        counts[stage] = count
    except sqlite3.OperationalError:
        pass  # Initial creation may overlap a progress poll; retry on the next poll.
    return counts


def export_stage(root, stage, destination, *, overwrite=True):
    if stage not in STAGES:
        raise ValueError('Unknown dataset stage')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    count = 0
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            for record in stage_records(root, stage):
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
                count += 1
        if overwrite:
            temporary.replace(destination)
        else:
            destination.hardlink_to(temporary)  # Atomic no-clobber publication on the same filesystem.
    finally:
        temporary.unlink(missing_ok=True)
    return count


def export_stages(root, directory):
    return {stage: {'rows': export_stage(root, stage, Path(directory) / (stage + '.jsonl')),
                    'path': str(Path(directory) / (stage + '.jsonl'))} for stage in STAGES}


def review_counts(root):
    path = Path(root) / 'qa_stages.sqlite'
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            return dict(db.execute("SELECT json_extract(record,'$.status'),COUNT(*) FROM artifacts WHERE stage='candidates' GROUP BY json_extract(record,'$.status')"))
    except sqlite3.OperationalError:
        return {}
