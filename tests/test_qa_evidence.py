import json
from types import SimpleNamespace

import pytest

from dataflowwebagent import qa_pipeline
from dataflowwebagent.qa_artifacts import save_stage, stage_records, export_stage, stage_counts
from dataflowwebagent.agents.Obtainer.datamixer.operators.evidence import (
    EvidencePrepare, EvidenceSelect, EvidenceQAGenerate, EvidenceQAReview, split_sections,
)
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore
from qa_fixtures import approved_content, evidence_response


def test_cleaning_keeps_late_sections_lists_and_table_relationships(tmp_path):
    text = '<html><head><title>title</title></head><nav>navigation noise</nav><main><h1>Guide</h1>'
    text += '<p>' + ('Introductory material. ' * 800) + '</p>'
    text += '<h2>Late evidence</h2><ul><li>Risk may increase.</li></ul><table><tr><th>Year</th><th>Rate</th></tr><tr><td>2025</td><td>4%</td></tr></table></main><footer>footer noise</footer></html>'
    row = {'content': {'html': text, 'url': 'https://example.org/guide', 'title': 'Guide'}}
    result = EvidencePrepare().process([row], SimpleNamespace(root=tmp_path))[0]['content']
    assert 'noise' not in result['text']
    assert result['source_url'] == 'https://example.org/guide'
    assert '2025 | 4%' in result['text']
    assert any(s['section'] == 'Late evidence' and 'Risk may increase' in s['text'] for s in result['segments'])
    assert len(result['segments']) > 3 and all(len(s['text']) <= 5000 for s in result['segments'])


def source_row(text='Some people may develop a condition. This is not a guarantee.'):
    segments = split_sections(text)
    return {'content': {'title': 'Health', 'source_url': 'https://example.org/health', 'text': text,
                        'segments': segments, 'selected_segments': segments}}


def test_rejected_and_fabricated_selection_evidence_is_archived(tmp_path):
    row = source_row()
    op = EvidenceSelect(instruction='Hypertension education')
    op.ask = lambda *args: {'segments': [{'segment_id': row['content']['segments'][0]['segment_id'],
        'relevant': True, 'reason': 'Claims relevance but fabricated quote', 'evidence_quote': 'This quote is not in the source at all.',
        'context': {'standard': 'Invented standard'}}]}
    assert op.process([row], SimpleNamespace(root=tmp_path)) == []
    saved = list(stage_records(tmp_path, 'source-review'))
    assert not saved[0]['accepted']
    assert saved[0]['selection'][0]['context']['standard'] == ''


def test_multiple_candidates_archived_before_review_and_zero_is_allowed(tmp_path):
    row = source_row()
    quote = row['content']['text']
    op = EvidenceQAGenerate(instruction='Health QA')
    op.ask = lambda *args: {'items': [{'question': f'Question {n}?', 'answer': 'It may occur.', 'knowledge_point': str(n),
                                       'claims': [{'claim': 'It may occur.', 'quote': quote}]} for n in range(2)]}
    result = op.process([row], SimpleNamespace(root=tmp_path))
    assert len(result[0]['content']['qa_candidates']) == 2
    assert all(c['status'] == 'unreviewed' for c in stage_records(tmp_path, 'candidates'))
    op.ask = lambda *args: {'items': []}
    assert op.process([source_row()], SimpleNamespace(root=tmp_path))[0]['content']['qa_candidates'] == []


def generated_row(tmp_path):
    row = source_row()
    op = EvidenceQAGenerate()
    op.ask = lambda *args: {'items': [{'question': 'Is it guaranteed?', 'answer': 'It may occur.', 'knowledge_point': 'risk',
                                      'claims': [{'claim': 'It may occur.', 'quote': row['content']['text']}]}]}
    return op.process([row], SimpleNamespace(root=tmp_path))[0]


@pytest.mark.parametrize('issue', ['unsupported', 'bad_quote', 'missing_field', 'context', 'conflict'])
def test_review_fails_closed_and_keeps_candidates(tmp_path, issue):
    row = generated_row(tmp_path)
    review = {'topic_match': True, 'all_claims_supported': True, 'context_complete': True, 'conflict': False,
              'claims': [{'claim': 'It may occur.', 'verdict': 'supported', 'quote': source_row()['content']['text']}]}
    if issue == 'unsupported': review['claims'][0]['verdict'] = 'source_gap'
    if issue == 'bad_quote': review['claims'][0]['quote'] = 'Invented evidence quote.'
    if issue == 'missing_field': del review['topic_match']
    if issue == 'context': review['context_complete'] = False
    if issue == 'conflict': review['conflict'] = True
    op = EvidenceQAReview(); op.model_name = 'test'; op.ask = lambda *args: review
    op.process([row], SimpleNamespace(root=tmp_path))
    assert row['content']['review_complete']
    assert list(stage_records(tmp_path, 'candidates'))[0]['status'] == 'needs_review'


def test_missing_generation_quote_gets_one_repair_without_automatic_approval(tmp_path):
    row = generated_row(tmp_path)
    row['content']['qa_candidates'][0]['claims'][0]['quote'] = 'Nonexistent quote'
    op = EvidenceQAReview()
    calls = []
    def repair(*args):
        calls.append(args)
        return {'claims': [{'claim': 'It may occur.', 'quote': 'Still nonexistent quote'}]}
    op.ask = repair
    op.process([row], SimpleNamespace(root=tmp_path))
    assert row['content']['qa_candidates'][0]['status'] == 'needs_review'
    assert len(calls) == 1 and row['content']['qa_candidates'][0]['quote_repair_attempted']


def test_cross_source_conflict_holds_previous_candidate_out_of_final_count(tmp_path):
    store = DataStore.init(tmp_path)
    try:
        old = approved_content('A source-supported question?', 'An earlier answer.')
        previous = old['qa_candidates'][0]
        save_stage(tmp_path, 'candidates', previous['candidate_id'], previous)
        did = store.catalog.add_dataset(name='qa', source='test')
        store.ingest_records(did, [{'content': old}], defaults={'quality_level': 'L3'}, decontaminate=False)
        row = generated_row(tmp_path)
        op = EvidenceQAReview();op.model_name = 'test'
        op.ask = lambda *args: {'topic_match': True, 'all_claims_supported': True, 'context_complete': True,
            'conflict': True, 'conflicting_ids': [previous['candidate_id']], 'reason': 'Conflicting sources',
            'claims': [{'claim': 'It may occur.', 'verdict': 'supported', 'quote': source_row()['content']['text']}]}
        op.process([row], SimpleNamespace(root=tmp_path))
        assert qa_pipeline.qa_records(store, 'qa')[0] == []
        assert all(c['status'] == 'needs_review' for c in stage_records(tmp_path, 'candidates'))
    finally:
        store.close()


def test_unverified_legacy_data_cannot_count_as_reviewed(tmp_path):
    store = DataStore.init(tmp_path)
    try:
        did = store.catalog.add_dataset(name='legacy', source='test')
        store.ingest_records(did, [{'content': {'messages': [{'role': 'user', 'content': 'Question?'},
                                                             {'role': 'assistant', 'content': 'Answer.'}]}}],
                             defaults={'quality_level': 'L3'})
        rows, _, metrics = qa_pipeline.qa_records(store, 'legacy')
        assert rows == [] and metrics['unverified_rows_removed'] == 1
    finally:
        store.close()


def test_raw_export_is_lossless_and_does_not_require_final_qa(tmp_path):
    store = DataStore.init(tmp_path / 'warehouse')
    try:
        did = store.catalog.add_dataset(name='raw', source='test')
        html = '<html><h1>Original</h1><p>Evidence &amp; context.</p></html>'
        store.ingest_records(did, [{'content': {'html': html, 'url': 'https://example.org'}}], defaults={'quality_level': 'L1'})
        assert export_stage(store.root, 'raw', tmp_path / 'raw.jsonl') == 1
        assert json.loads((tmp_path / 'raw.jsonl').read_text())['content']['html'] == html
        assert stage_counts(store.root)['corpus'] == 0
        destination = tmp_path / 'new-raw.jsonl'
        assert export_stage(store.root, 'raw', destination, overwrite=False) == 1
        destination.write_text('existing user data\n')
        with pytest.raises(FileExistsError):
            export_stage(store.root, 'raw', destination, overwrite=False)
        assert destination.read_text() == 'existing user data\n'
        assert not list(tmp_path.glob('*.tmp'))
    finally:
        store.close()


@pytest.mark.parametrize('stop_after,expected_calls', [('raw', 0), ('corpus', 1), ('qa', 3)])
def test_source_only_modes_never_generate_qa(tmp_path, monkeypatch, stop_after, expected_calls):
    from dataflowwebagent.agents.Obtainer.datamixer import llm
    from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
    from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as web
    from dataflowwebagent.agents.Obtainer.datamixer.webagents.campaign import LLMQueryExpander, ExpandedQuery
    root = tmp_path / 'warehouse'
    DataStore.init(root).close()
    pool = ModelPool(root);pool.add(ModelSpec(name='test', api_url='https://model.invalid'));pool.set_default('test')
    monkeypatch.setattr(LLMQueryExpander, 'expand', lambda self, q, count: ([ExpandedQuery(query=q)], []))
    monkeypatch.setattr(web.ToolCallingWebAgentKernel, 'discover', lambda *args: (['https://example.org'], [], 1))
    text = 'Calling a generator function returns an iterator. ' * 8
    monkeypatch.setattr(web.WebPageFetcher, 'fetch', lambda self, url: web.FetchedPage(
        requested_url=url, final_url=url, html=f'<html><main><p>{text}</p></main></html>', title='Generators',
        text_preview=text, status=200, content_type='text/html', headers={}, fetch_mode='mock'))
    calls = []
    def post(url, payload, key, timeout):
        calls.append(payload)
        return {'choices': [{'message': {'content': json.dumps(evidence_response(payload['messages']))}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    monkeypatch.setattr(llm, '_post', post)
    report = qa_pipeline.run_qa('Create 1 QA about generators', warehouse=root, run=tmp_path / 'run',
                               output=tmp_path / 'qa.jsonl', max_pages=1, stop_after=stop_after)
    assert report['status'] == 'completed'
    assert len(calls) == expected_calls
    assert report['artifacts']['raw']['rows'] == 1
    assert (tmp_path / 'qa.jsonl').exists() == (stop_after == 'qa')
    if stop_after != 'raw': assert report['artifacts']['corpus']['rows'] == 1


def test_reference_span_repair_requires_ordered_exact_fragments():
    from dataflowwebagent.agents.Obtainer.datamixer.operators.evidence import restore_quote_spans, quote_present
    text = 'First verified statement. Relevant condition in between. Second verified statement.'
    claim = {'claim': 'Example', 'verdict': 'source_gap', 'quote': 'First verified statement. ... Second verified statement.'}
    repaired = restore_quote_spans([claim], text)[0]
    assert repaired['quote'] == text and repaired['verdict'] == 'source_gap'
    assert repaired['original_quote'] == claim['quote']
    wrong = {**claim, 'quote': 'Second verified statement. ... First verified statement.'}
    assert not quote_present(restore_quote_spans([wrong], text)[0]['quote'], text)


def test_over_budget_model_output_kept_but_not_reviewed(tmp_path):
    row = source_row()
    quote = row['content']['text']
    op = EvidenceQAGenerate()
    op.ask = lambda *args: {'items': [{'question': f'Question {i}', 'answer': 'It may occur.', 'knowledge_point': str(i),
                                     'claims': [{'claim': 'It may occur.', 'quote': quote}]} for i in range(4)]}
    result = op.process([row], SimpleNamespace(root=tmp_path))[0]
    assert len(result['content']['qa_candidates']) == 2
    archive = list(stage_records(tmp_path, 'candidates'))
    assert len(archive) == 4
    assert sum(c['status'] == 'needs_review' for c in archive) == 2
