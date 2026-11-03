"""Validate the fixed local service contract without loading weights or using a GPU."""
import unittest

from scripts.local_model.serve_mlx import validate_request


class LocalMlxContractTests(unittest.TestCase):
    def payload(self):
        return {'model': 'local', 'messages': [{'role': 'user', 'content': '合成资料'}],
                'max_tokens': 32, 'stream': False}

    def test_accepts_fixed_text_model(self):
        messages, maximum = validate_request(self.payload(), 'local', 128)
        self.assertEqual(messages[0]['content'], '合成资料')
        self.assertEqual(maximum, 32)

    def test_rejects_other_model_and_unimplemented_generation_options(self):
        for change in ({'model': '/other/model'}, {'stream': 'true'}, {'n': 2},
                       {'temperature': 0.5}, {'tools': []}, {'max_tokens': True},
                       {'max_tokens': 129}, {'max_completion_tokens': 30}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_request(self.payload() | change, 'local', 128)

    def test_accepts_explicit_sse_mode(self):
        messages,maximum=validate_request(self.payload() | {'stream':True},'local',128)
        self.assertEqual(maximum,32)
        self.assertEqual(messages[0]['content'],'合成资料')

    def test_rejects_multimodal_and_oversized_messages(self):
        for messages in ([], [{'role': 'user', 'content': [{'type': 'image_url'}]}],
                         [{'role': 'tool', 'content': 'x'}],
                         [{'role': 'user', 'content': 'x' * 20001}],
                         [{'role': 'user', 'content': 'x'}] * 33):
            with self.subTest(messages=str(messages)[:80]), self.assertRaises(ValueError):
                validate_request(self.payload() | {'messages': messages}, 'local', 128)


if __name__ == '__main__':
    unittest.main()
