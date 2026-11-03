"""Bounded complete-body reads; a short network read is not a complete source."""
import time

def read_complete(response,limit,timeout=60):
 declared=response.headers.get('Content-Length');expected=int(declared) if declared is not None else None
 if expected is not None and not 0<=expected<=limit:raise ValueError('Source exceeds download budget')
 chunks=[];size=0;started=time.monotonic()
 while True:
  if time.monotonic()-started>timeout:raise TimeoutError('Source download time budget exceeded')
  block=response.read(min(65536,limit+1-size))
  if not block:break
  size+=len(block)
  if size>limit:raise ValueError('Source exceeds download budget')
  chunks.append(block)
 if expected is not None and size!=expected:raise ValueError(f'Incomplete source body: expected {expected}, received {size}')
 return b''.join(chunks)
