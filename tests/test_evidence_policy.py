import unittest
from unittest.mock import Mock,patch

from app.runtime.evidence_policy import prefix_table_refusal
from app.runtime.models import CompatibleRagChain
from app.runtime.streaming import generate_stream


class EvidencePolicyTests(unittest.TestCase):
    # Fictional entries ensure the policy reads source cells, not known SI values.
    table='<table><tr><td>Factor</td><td>Name</td><td>Symbol</td></tr><tr><td>10^37</td><td>fictiona</td><td>Zz</td></tr></table>'

    def test_lookup_copies_fictional_cells_and_citation_without_generalizing_symbol_rules(self):
        chain=CompatibleRagChain.__new__(CompatibleRagChain);chain._post=Mock(side_effect=AssertionError('Extract source, no model'))
        question='词头 fictiona 的因数和符号？'
        answer=chain.generate(question,[self.table],['test-source'])
        self.assertEqual(answer,'按来源表逐项提取：\n- fictiona：因数 10^37；符号 Zz。来源：test-source')
        self.assertEqual(chain.last_call['reason'],'source_table_lookup');chain._post.assert_not_called()
        pieces=[]
        with patch('app.runtime.streaming.build_opener',side_effect=AssertionError('No stream call allowed')):
            self.assertEqual(generate_stream(chain,question,[self.table],['test-source'],[],pieces.append),answer)
        self.assertEqual(''.join(pieces),answer)

    def test_complete_named_row_allows_generation_but_missing_and_conflicting_rows_do_not(self):
        self.assertIsNone(prefix_table_refusal('SI词头 fictiona 的因数及符号？',[self.table]))
        missing=prefix_table_refusal('SI词头 fictiona 和 absentia 的因数及符号？',[self.table])
        self.assertIn('absentia',missing);self.assertNotIn('10^37',missing)
        self.assertIsNotNone(prefix_table_refusal('SI词头 fictiona 的符号？',[self.table,self.table.replace('Zz','zz')]))

    def test_prose_name_or_wrong_column_order_cannot_substitute_a_row(self):
        self.assertIsNotNone(prefix_table_refusal('SI词头 fictiona 的因数？',['fictiona 10^37 Zz']))
        self.assertIsNotNone(prefix_table_refusal('SI词头 fictiona 的因数？',[self.table.replace('Factor','Price')]))
        self.assertIsNone(prefix_table_refusal('解释设备维护',[self.table]))
        # Two different tables cannot lend each other their headers.
        header=self.table.split('<tr><td>10^')[0]+'</table>'
        other=self.table.replace('Factor','Price')
        self.assertIsNotNone(prefix_table_refusal('SI词头 fictiona 的因数？',[header+other]))
        self.assertIsNotNone(prefix_table_refusal('SI词头 fictiona 的因数？',
            [self.table.replace('10^37','10<sup>37</sup>')]))

    def test_sync_and_stream_refuse_missing_rows_without_a_model_call_or_fake_reason(self):
        chain=CompatibleRagChain.__new__(CompatibleRagChain);chain._post=Mock(side_effect=AssertionError('No model call allowed'))
        question='SI词头 absentia 的因数与符号？'
        answer=chain.generate(question,[self.table],['source-one'])
        self.assertIn('无法依据本轮证据',answer);self.assertNotIn('最大',answer);self.assertNotIn('最小',answer)
        self.assertFalse(chain.last_call['called']);chain._post.assert_not_called()
        chunks=[]
        with patch('app.runtime.streaming.build_opener',side_effect=AssertionError('No stream call allowed')):
            streamed=generate_stream(chain,question,[self.table],['source-one'],[],chunks.append)
        self.assertEqual(answer,streamed);self.assertEqual(''.join(chunks),answer)
