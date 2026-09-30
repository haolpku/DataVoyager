import json

import pytest
from qa_fixtures import approved_content, evidence_response

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
    monkeypatch.setattr(qa_pipeline, '_collect_hf_records',
                        lambda *args, **kwargs: ([{'content': {'text': 'dataset source text', 'source_url': 'https://huggingface.co/datasets/example/data'}}], {'status': 'loaded', 'records_loaded': 1, 'source': 'huggingface'}))

    def start(self, request, config, **kwargs):
        index = len(calls)
        assert kwargs.get('collect_web') is False
        calls.append({'request': request, 'budget': config.webagent_config['max_pages']})
        store = DataStore.open(warehouse)
        try:
            for level, name in [('L2', config.l2_dataset), ('L3', config.l3_dataset)]:
                did = store.catalog.resolve_dataset(name) or store.catalog.add_dataset(name=name, source='fixture')
                records = []
                for question, answer in batches[min(index, len(batches) - 1)]:
                    url = f'https://example.org/{index}/{len(records)}'
                    content = {'text': answer, 'source_url': url} if level == 'L2' else approved_content(question, answer, url=url)
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
        [('First question?', 'First answer.'), ('Second question?', 'Second answer.')],
    ])
    result = qa_pipeline.run_qa('Create 2 QA pairs', warehouse=warehouse, run=tmp_path / 'run',
                                output=tmp_path / 'qa.jsonl', max_pages=10)
    assert result['status'] == 'completed' and result['target_met']
    assert result['rows'] == result['eligible_rows'] == 2
    assert len(calls) == 1 and calls[0]['budget'] == 10
    assert result['rounds'][0]['new_rows'] == 2
    sources = [json.loads(s) for s in (tmp_path / 'qa.jsonl.sources.jsonl').read_text().splitlines()]
    assert [s['row'] for s in sources] == [1, 2]
    assert sources[0]['source_url'] == 'https://example.org/0/0'


def test_no_growth_pauses_without_faking_completion(tmp_path, monkeypatch):
    warehouse = tmp_path / 'warehouse'
    calls = fixture_campaign(monkeypatch, warehouse, [[('Same question?', 'Same answer.')]])
    result = qa_pipeline.run_qa('生成100条QA', warehouse=warehouse, run=tmp_path / 'run',
                                output=tmp_path / 'qa.jsonl', max_pages=100)
    assert len(calls) == 1
    assert result['status'] == 'needs_confirmation'
    assert result['stop_reason'] == 'dataset_exhausted'
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
    assert result['stop_reason'] == 'dataset_exhausted'
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
    assert preview['target_rows'] == 100 and preview['max_source_rows'] == 50
    monkeypatch.setattr(qa_pipeline, 'run_qa', lambda *a, **kw: {'status': 'needs_confirmation', 'rows': 4, 'target_rows': 100})
    assert voyager.main(['build', '生成100条QA', '--output', str(tmp_path / 'qa.jsonl')]) == 2


def test_real_dataset_campaign_never_calls_web_search(tmp_path, monkeypatch):
    from dataflowwebagent.agents.Obtainer.datamixer import llm
    from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as web
    warehouse = tmp_path / 'warehouse'
    store = DataStore.init(warehouse); store.close()
    pool = ModelPool(warehouse); pool.add(ModelSpec(name='test', api_url='https://model.invalid')); pool.set_default('test')
    monkeypatch.setattr(qa_pipeline, '_collect_hf_records', lambda *a, **k: (
        [{'content': {'text': 'A generator returns an iterator and pauses at yield. ' * 6,
                      'source_url': 'https://huggingface.co/datasets/example/generators'}}],
        {'status': 'loaded', 'records_loaded': 1, 'source': 'huggingface'}))
    monkeypatch.setattr(web.ToolCallingWebAgentKernel, 'discover', lambda *a, **k: pytest.fail('web search called'))
    def post(url, payload, key, timeout):
        item = evidence_response(payload['messages'], question='When does a generator resume?', answer='When the next value is requested.')
        return {'choices': [{'message': {'content': json.dumps(item)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    monkeypatch.setattr(llm, '_post', post)
    result = qa_pipeline.run_qa('Create 1 QA pairs about generators', warehouse=warehouse,
                                run=tmp_path / 'run', output=tmp_path / 'qa.jsonl', max_pages=10)
    assert result['status'] == 'completed' and result['rows'] == 1
    assert result['source_acquisition']['datasets']['records_loaded'] == 1
    assert len(result['rounds']) == 1


@pytest.mark.parametrize('stage_failed', [False, True])
def test_empty_filtering_is_shortfall_but_provider_failures_stay_failed(tmp_path, monkeypatch, stage_failed):
    warehouse = tmp_path / 'warehouse'
    fixture_campaign(monkeypatch, warehouse, [[]])
    def start(*args, **kwargs):
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
