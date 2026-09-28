import json

import pytest

from dataflowwebagent import qa_pipeline, voyager
from dataflowwebagent.qa_quantity import infer_target, resolve_target
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore
from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool, ModelSpec


@pytest.mark.parametrize(('prompt', 'count'), [
    ('构建1000条医疗QA，采集50页', 1000), ('生成一百道题', 100),
    ('做两千个问答', 2000), ('Create 1,000 QA pairs using 80 pages', 1000),
    ('生成20条问答，回答2至5句话', 20), ('采集100页，参考2026年报告', None),
    ('先做100条QA，再做1000条QA', None),
])
def test_explicit_question_count_is_not_page_budget(prompt, count):
    assert infer_target(prompt) == count


def test_invalid_target_is_not_silently_coerced():
    for invalid in (True, False, 1.5, -1, 10001, '100'):
        with pytest.raises(ValueError):
            resolve_target('QA', invalid)


def fixture_campaign(monkeypatch, warehouse, batches):
    store = DataStore.init(warehouse)
    store.close()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name='test', api_url='https://model.invalid'))
    pool.set_default('test')
    calls = []

    def start(self, request, config):
        index = len(calls)
        calls.append({'request': request, 'budget': config.webagent_config['max_pages']})
        store = DataStore.open(warehouse)
        try:
            for level, name in [('L2', config.l2_dataset), ('L3', config.l3_dataset)]:
                did = store.catalog.resolve_dataset(name) or store.catalog.add_dataset(name=name, source='fixture')
                records = []
                for question, answer in batches[min(index, len(batches) - 1)]:
                    url = f'https://example.org/{index}/{len(records)}'
                    content = {'text': answer, 'source_url': url} if level == 'L2' else {
                        'messages': [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}],
                        'provenance': {'source_url': url}}
                    records.append({'content': content})
                store.ingest_records(did, records, defaults={'quality_level': level}, decontaminate=False)
        finally:
            store.close()
        return {'status': 'completed', 'pipeline': {'ok': True}, 'run_id': f'round-{index + 1}'}

    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, 'start', start)
    return calls


def test_refills_to_target_deduplicates_questions_and_caps_export(tmp_path, monkeypatch):
    warehouse = tmp_path / 'warehouse'
    calls = fixture_campaign(monkeypatch, warehouse, [
        [('First question?', 'First answer.'), ('First question?', 'Different answer.')],
        [('Second question?', 'Second answer.'), ('Third question?', 'Third answer.')],
    ])
    result = qa_pipeline.run_qa('Create 2 QA pairs', warehouse=warehouse, run=tmp_path / 'run',
                                output=tmp_path / 'qa.jsonl', max_pages=10)
    assert result['status'] == 'completed' and result['target_met']
    assert result['rows'] == 2 and result['eligible_rows'] == 3
    assert len(calls) == 2 and sum(c['budget'] for c in calls) <= 10
    assert 'https://example.org/0/0' in calls[1]['request']
    assert result['rounds'][1]['new_rows'] == 2
    sources = [json.loads(s) for s in (tmp_path / 'qa.jsonl.sources.jsonl').read_text().splitlines()]
    assert [s['row'] for s in sources] == [1, 2]
    assert sources[0]['source_url'] == 'https://example.org/0/0'


def test_no_growth_pauses_without_faking_completion(tmp_path, monkeypatch):
    warehouse = tmp_path / 'warehouse'
    calls = fixture_campaign(monkeypatch, warehouse, [[('Same question?', 'Same answer.')]])
    result = qa_pipeline.run_qa('生成100条QA', warehouse=warehouse, run=tmp_path / 'run',
                                output=tmp_path / 'qa.jsonl', max_pages=100)
    assert len(calls) == 3  # first yield, then two rounds without a new question
    assert result['status'] == 'needs_confirmation'
    assert result['stop_reason'] == 'no_new_questions'
    assert result['rows'] == 1 and result['shortfall'] == 99
    progress = json.loads((tmp_path / 'run/progress.json').read_text())
    assert progress['status'] == 'needs_confirmation' and progress['generated_rows'] == 1
    assert progress['target_rows'] == 100


def test_budget_shortfall_and_zero_yield_keep_honest_reports(tmp_path, monkeypatch):
    warehouse = tmp_path / 'warehouse'
    calls = fixture_campaign(monkeypatch, warehouse, [[]])
    result = qa_pipeline.run_qa('Create 100 QA pairs', warehouse=warehouse, run=tmp_path / 'run',
                                output=tmp_path / 'qa.jsonl', max_pages=1)
    assert len(calls) == 1
    assert result['status'] == 'needs_confirmation' and result['rows'] == 0
    assert result['stop_reason'] == 'page_budget_exhausted'
    assert not (tmp_path / 'qa.jsonl').exists()


def test_continue_preserves_previous_qa_and_files(tmp_path, monkeypatch):
    previous = tmp_path / 'previous'
    fixture_campaign(monkeypatch, previous, [[('Original question?', 'Original answer.')]])
    first = qa_pipeline.run_qa('Create 2 QA pairs', warehouse=previous, run=tmp_path / 'first',
                              output=tmp_path / 'first.jsonl', max_pages=1)
    assert first['status'] == 'needs_confirmation'
    before = (tmp_path / 'first.jsonl').read_bytes(), (previous / 'catalog.db').read_bytes()
    warehouse = tmp_path / 'warehouse'
    calls = fixture_campaign(monkeypatch, warehouse, [[('New question?', 'New answer.')]])
    result = qa_pipeline.run_qa('Create 2 QA pairs', warehouse=warehouse, run=tmp_path / 'second',
                                output=tmp_path / 'second.jsonl', max_pages=1, base_warehouse=previous)
    assert result['target_met'] and result['rows'] == 2 and len(calls) == 1
    assert json.loads((tmp_path / 'second.jsonl').read_text().splitlines()[0])['instruction'] == 'Original question?'
    assert before == ((tmp_path / 'first.jsonl').read_bytes(), (previous / 'catalog.db').read_bytes())


def test_cli_shortfall_returns_nonzero_and_dry_run_shows_target(tmp_path, monkeypatch, capsys):
    assert voyager.main(['build', '生成100条QA', '--output', str(tmp_path / 'qa.jsonl'), '--dry-run']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['target_rows'] == 100 and preview['max_pages'] == 20
    monkeypatch.setattr(qa_pipeline, 'run_qa', lambda *a, **kw: {'status': 'needs_confirmation', 'rows': 4, 'target_rows': 100})
    assert voyager.main(['build', '生成100条QA', '--output', str(tmp_path / 'qa.jsonl')]) == 2


def test_real_campaign_refills_across_rounds(tmp_path, monkeypatch):
    from dataflowwebagent.agents.Obtainer.datamixer import llm
    from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as web
    from dataflowwebagent.agents.Obtainer.datamixer.webagents.campaign import LLMQueryExpander, ExpandedQuery
    warehouse = tmp_path / 'warehouse'
    store = DataStore.init(warehouse)
    store.close()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name='test', api_url='https://model.invalid'))
    pool.set_default('test')
    discovered = []
    monkeypatch.setattr(LLMQueryExpander, 'expand', lambda self, query, count: ([ExpandedQuery(query=query)], []))
    def discover(self, query, tools):
        discovered.append(query)
        return [f'https://example.org/page-{len(discovered)}'], [], 1
    monkeypatch.setattr(web.ToolCallingWebAgentKernel, 'discover', discover)
    source = ('A generator returns an iterator. Its execution pauses at yield and resumes on next. ' * 6)
    def fetch(self, url):
        text = source + 'Page ' + url
        return web.FetchedPage(requested_url=url, final_url=url,
                               html=f'<html><title>{url}</title><body><p>{text}</p></body></html>',
                               title=url, text_preview=text, status=200, content_type='text/html', headers={}, fetch_mode='mock')
    monkeypatch.setattr(web.WebPageFetcher, 'fetch', fetch)
    def post(url, payload, key, timeout):
        prompt = payload['messages'][-1]['content']
        if 'Allowed labels:' in prompt:
            item = {'index': 0, 'labels': ['code'], 'confidence': .99, 'semantic_signals': [
                {'type': 'iterator', 'evidence': 'A generator returns an iterator.', 'confidence': .99},
                {'type': 'execution', 'evidence': 'Its execution pauses at yield and resumes on next.', 'confidence': .99}]}
        else:
            item = {'index': 0, 'question': f'Question about generator behavior number {len(discovered)}?',
                    'answer': 'A generator returns an iterator.'}
        return {'choices': [{'message': {'content': json.dumps({'results': [item]})}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    monkeypatch.setattr(llm, '_post', post)
    result = qa_pipeline.run_qa('Create 3 QA pairs about generators', warehouse=warehouse,
                                run=tmp_path / 'run', output=tmp_path / 'qa.jsonl', max_pages=10)
    assert result['status'] == 'completed' and result['rows'] == 3
    assert len(discovered) == 3 and len(result['rounds']) == 3
    assert result['usage']['calls'] == 6
    progress = json.loads((tmp_path / 'run/progress.json').read_text())
    assert progress['sources_accepted'] == 3 and progress['qa_candidates'] == 3


@pytest.mark.parametrize('stage_failed', [False, True])
def test_empty_filtering_is_shortfall_but_provider_failures_stay_failed(tmp_path, monkeypatch, stage_failed):
    warehouse = tmp_path / 'warehouse'
    fixture_campaign(monkeypatch, warehouse, [[]])
    def start(*args):
        return {'run_id': 'empty', 'status': 'completed_with_errors', 'queue': {'failed': 0, 'pending': 0, 'running': 0},
                'pipeline': {'status': 'completed', 'ok': False, 'error': 'no records materialized for levels: L3',
                             'stages': [{'failed': 1 if stage_failed else 0}]}}
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, 'start', start)
    kwargs = dict(warehouse=warehouse, run=tmp_path / 'run', output=tmp_path / 'qa.jsonl', max_pages=1)
    if stage_failed:
        with pytest.raises(RuntimeError):
            qa_pipeline.run_qa('Create 100 QA pairs', **kwargs)
    else:
        assert qa_pipeline.run_qa('Create 100 QA pairs', **kwargs)['status'] == 'needs_confirmation'
