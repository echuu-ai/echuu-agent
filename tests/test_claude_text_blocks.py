from types import SimpleNamespace as S
import pytest
from echuu.live.llm_client import LLMClient

def client(blocks):
 c=LLMClient.__new__(LLMClient);c.model='test';c.client=S(messages=S(create=lambda **kw:S(content=blocks)));return c

def test_thinking_block_does_not_hide_actual_response():
 assert client([S(type='thinking',thinking='internal'),S(type='text',text='{"ok":'),S(type='text',text='true}')]).call('test')=='{"ok":true}'

def test_empty_text_is_not_success():
 with pytest.raises(RuntimeError,match='no text blocks'):client([S(type='thinking')]).call('test')

def test_sonnet_structured_request_disables_thinking_and_passes_schema():
 captured={}
 def create(**kw):
  captured.update(kw)
  return S(content=[S(type='text',text='{"lines":[]}')],stop_reason='end_turn')
 c=client([]);c.model='claude-sonnet-5';c.client.messages.create=create
 schema={'type':'object','properties':{},'additionalProperties':False}
 assert c.call_structured('test',response_schema=schema)=='{"lines":[]}'
 assert captured['thinking']=={'type':'disabled'}
 assert captured['output_config']['format']['schema']==schema

def test_truncated_text_is_not_passed_to_json_parser():
 c=client([])
 c.client.messages.create=lambda **kw:S(content=[S(type='text',text='{"lines":')],stop_reason='max_tokens')
 with pytest.raises(RuntimeError,match='truncated'):c.call('test')
