"""Durable stage snapshots. Reads never open a mutable DataStore on a live run."""
from contextlib import closing
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .agents.Obtainer.datamixer.cas import ContentStore

STAGES = {'raw': '收集来源', 'merged': '合并去重后数据', 'corpus': '清洗后正文与证据',
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
            query = ('SELECT s.sample_id,s.cid,s.tags_json,s.created_at FROM samples s '
                     'JOIN datasets d ON d.id=s.dataset_id WHERE s.quality_level=? '
                     + ("AND d.name NOT LIKE '%_merged' " if stage == 'raw' else '')
                     + 'ORDER BY s.created_at,s.sample_id')
            for r in db.execute(query,
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


def _merge_text(record):
    content = record.get('content') if isinstance(record, dict) else None
    if not isinstance(content, dict):
        return ''
    text = content.get('text') or content.get('document') or content.get('html') or ''
    if not isinstance(text, str):
        return ''
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    return re.sub(r'\s+', ' ', text).strip().casefold()


def build_merged_stage(root):
    """Materialize a deterministic union of collected records, coalescing exact text duplicates."""
    root = Path(root)
    merged = {}
    for record in stage_records(root, 'raw') or ():
        key_text = _merge_text(record)
        # Preserve short structured records too; use the canonical record when no body exists.
        key = hashlib.sha256((key_text or json.dumps(record.get('content', {}), ensure_ascii=False,
                                                       sort_keys=True)).encode()).hexdigest()
        if key not in merged:
            merged[key] = {**record, 'merge': {'duplicate_count': 1, 'source_records': [record.get('metadata', {})]}}
        else:
            item = merged[key]
            item['merge']['duplicate_count'] += 1
            item['merge']['source_records'].append(record.get('metadata', {}))
    path = root / 'qa_stages.sqlite'
    if path.exists():
        with closing(sqlite3.connect(path, timeout=30)) as db:
            db.execute('DELETE FROM artifacts WHERE stage=?', ('merged',))
            db.commit()
    for key, record in merged.items():
        save_stage(root, 'merged', key, record)
    return {'rows': len(merged), 'duplicates_removed': max(0, sum(r['merge']['duplicate_count'] for r in merged.values()) - len(merged))}


def materialize_merged_dataset(root, dataset_name):
    """Persist the merged stage as an L1 dataset for downstream cleaning/export."""
    from .agents.Obtainer.datamixer.store import DataStore
    store = DataStore.open(root)
    try:
        dataset_id = store.catalog.resolve_dataset(dataset_name)
        if dataset_id is None:
            dataset_id = store.catalog.add_dataset(name=dataset_name, source='merged_sources')
        else:
            store.catalog.erase(dataset_id=dataset_id, reason='rebuild merged dataset')
        records = ({'content': row.get('content'),
                    'tags': {**(row.get('metadata') or {}), 'merge': row.get('merge', {})}}
                   for row in stage_records(root, 'merged') or ())
        result = store.ingest_records(dataset_id, records, defaults={'quality_level': 'L1'}, decontaminate=False)
        return {'dataset': dataset_name, 'rows': result.written}
    finally:
        store.close()


def stage_counts(root):
    root = Path(root)
    counts = dict.fromkeys(STAGES, 0)
    try:
        if (root / 'catalog.db').exists():
            with closing(sqlite3.connect((root / 'catalog.db').resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                for level, count in db.execute("SELECT s.quality_level,COUNT(*) FROM samples s JOIN datasets d ON d.id=s.dataset_id WHERE s.quality_level IN ('L1','L2') AND d.name NOT LIKE '%_merged' GROUP BY s.quality_level"):
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
