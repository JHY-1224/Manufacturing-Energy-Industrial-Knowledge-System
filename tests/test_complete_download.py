import unittest
from scripts.collection.http_read import read_complete

class Response:
 def __init__(self,blocks,length=None):self.blocks=iter(blocks);self.headers={'Content-Length':str(length)} if length is not None else {}
 def read(self,n):return next(self.blocks,b'')

class CompleteDownloadTests(unittest.TestCase):
 def test_short_reads_are_accumulated(self):self.assertEqual(read_complete(Response([b'ab',b'c',b'def'],6),20),b'abcdef')
 def test_declared_length_detects_truncation(self):
  with self.assertRaisesRegex(ValueError,'Incomplete source body'):read_complete(Response([b'ab'],6),20)
 def test_declared_and_streamed_size_budgets(self):
  with self.assertRaises(ValueError):read_complete(Response([],50),20)
  with self.assertRaises(ValueError):read_complete(Response([b'abc',b'def']),4)
 def test_chunked_body_without_length(self):self.assertEqual(read_complete(Response([b'abc',b'def']),20),b'abcdef')
