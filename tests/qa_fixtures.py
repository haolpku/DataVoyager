"""Synthetic responses for exercising the real pipeline without network calls."""
import hashlib
import json


def approved_content(question, answer, title='', url='https://example.org/source'):
    return {'review_complete': True, 'qa_candidates': [{
        'candidate_id': hashlib.sha256(str((question, answer, title)).encode()).hexdigest(),
        'question': question, 'answer': answer, 'title': title, 'source_url': url,
        'status': 'source_supported', 'segment': {'text': str(answer)}, 'claims': [],
        'review': {'method': 'fixture'}}]}


def evidence_response(messages, question='What does calling a generator function return?', answer='It returns an iterator.'):
    payload = json.loads(messages[-1]['content'])
    if 'segments' in payload:
        return {'segments': [{'segment_id': s['segment_id'], 'relevant': True, 'reason': 'Directly answers topic',
                               'evidence_quote': s['text'][:160], 'topics': ['behavior'], 'context': {}}
                              for s in payload['segments']]}
    quote = payload['segment']['text'][:160]
    if 'qa' in payload:
        return {'topic_match': True, 'all_claims_supported': True, 'context_complete': True, 'conflict': False,
                'reason': 'Supported', 'claims': [{'claim': payload['qa']['answer'], 'verdict': 'supported', 'quote': quote}]}
    return {'items': [{'question': question, 'answer': answer, 'knowledge_point': 'generator behavior',
                       'claims': [{'claim': answer, 'quote': quote}]}]}
