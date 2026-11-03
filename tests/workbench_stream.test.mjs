import test from 'node:test';
import assert from 'node:assert/strict';
import {consumeEvents} from '../frontend/src/workbench-api.js';

test('SSE decoder accepts split UTF-8, comments, CRLF and multiple frames',async()=>{
  const bytes=new TextEncoder().encode(': heartbeat\r\n\r\nevent: token\r\ndata: {"type":"token","text":"制造"}\r\n\r\nevent: done\ndata: {"type":"done","answer":"制造"}\n\n');
  const events=[];
  await consumeEvents(new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(new Uint8Array([byte]));controller.close();}}),event=>events.push(event));
  assert.deepEqual(events.map(e=>e.type),['token','done']);assert.equal(events[0].text,'制造');
});
test('Truncated stream cannot be reported as a completed answer',async()=>{
  const stream=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"token","text":"部分"}\n\n'));c.close();}});
  await assert.rejects(consumeEvents(stream,()=>{}),/连接中断/);
});
test('Explicit failure event is preserved for message status',async()=>{
  const events=[];const stream=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"error","status":"failed"}\n\n'));c.close();}});
  await consumeEvents(stream,e=>events.push(e));assert.equal(events[0].status,'failed');
});
