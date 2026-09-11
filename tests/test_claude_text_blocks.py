from types import SimpleNamespace as S
import pytest
from echuu.live.llm_client import LLMClient

def client(blocks):
 c=LLMClient.__new__(LLMClient);c.model='test';c.client=S(messages=S(create=lambda **kw:S(content=blocks)));return c

def test_thinking_block_does_not_hide_actual_response():
 assert client([S(type='thinking',thinking='internal'),S(type='text',text='{"ok":'),S(type='text',text='true}')]).call('test')=='{"ok":true}'

def test_empty_text_is_not_success():
 with pytest.raises(RuntimeError,match='no text blocks'):client([S(type='thinking')]).call('test')
