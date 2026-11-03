import test from 'node:test';
import assert from 'node:assert/strict';
import {request} from '../frontend/src/workbench-api.js';

test('plain proxy 500 is actionable and preserves status',async t=>{
  t.mock.method(globalThis,'fetch',async()=>new Response('',{status:500}));
  await assert.rejects(request('/summary'),e=>e.status===500&&e.message.includes('本地服务暂时不可用'));
});
test('network failure is distinct from an empty knowledge base',async t=>{
  t.mock.method(globalThis,'fetch',async()=>{throw new TypeError('Failed to fetch');});
  await assert.rejects(request('/summary'),/无法连接本地服务/);
});
test('model failures preserve trace identifiers for the common diagnostics entry',async t=>{
  t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({detail:'Fixture model unavailable',trace_id:'trace_fixture123',stage:'embedding'}),{status:503,headers:{'Content-Type':'application/json'}}));
  await assert.rejects(request('/multimodal/search',{method:'POST',body:{text:'fixture'}}),e=>e.status===503&&e.traceId==='trace_fixture123'&&e.stage==='embedding'&&e.message.includes('质量与排错'));
});
test('timeout aborts the request and never retries a mutation',async t=>{
  let calls=0;t.mock.method(globalThis,'fetch',async(_url,{signal})=>{calls++;return new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));});
  await assert.rejects(request('/documents/text',{method:'POST',body:{content:'test'},timeout:10}),/响应超时/);
  assert.equal(calls,1);
});
