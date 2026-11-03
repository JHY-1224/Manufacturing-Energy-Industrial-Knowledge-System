import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import { createEmptyEvaluation, createEmptyState } from '../frontend/src/mock.js';
import { readApiResponse } from '../frontend/src/api.js';

// Exercise the actual SFC setup logic without a DOM or external dependencies.
// Vue template compilation and browser integration remain separate build checks.
const source = await readFile(new URL('../frontend/src/App.vue', import.meta.url), 'utf8');
const setup = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*;\n/gm, '');

function harness({ get, post, hash = '#/dashboard' } = {}) {
  let mounted;
  const listeners = new Map();
  const context = vm.createContext({
    ref: (value) => ({ value }),
    computed: (getter) => ({ get value() { return getter(); } }),
    onMounted: (callback) => { mounted = callback; },
    onUnmounted: () => {},
    createEmptyState, createEmptyEvaluation, readApiResponse,
    apiGet: get || (async () => { throw new Error('offline'); }),
    apiPost: post || (async () => { throw new Error('not configured'); }),
    apiDelete: async () => {}, URLSearchParams,
    window: {
      location: { hash }, scrollTo: () => {}, confirm: () => true,
      addEventListener: (name, callback) => listeners.set(name, callback),
      removeEventListener: (name) => listeners.delete(name),
    },
  });
  vm.runInContext(setup, context);
  return {
    read: (expression) => vm.runInContext(expression, context),
    mount: () => mounted(), listeners, context,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test('disconnected state contains no fabricated statistics, results, or scores', () => {
  const h = harness();
  assert.equal(h.read('state.value.documents.length'), 0);
  assert.equal(h.read('state.value.overview.kpis.length'), 0);
  assert.equal(h.read('retrievalResult.value.items.length'), 0);
  assert.equal(h.read('evaluationRuns.value.length'), 0);
  assert.equal(h.read('runtimeMode.value'), 'offline');
  for (const value of ['null', 'undefined', "''", 'NaN']) {
    assert.equal(h.read(`formatScore(${value})`), '—');
    assert.equal(h.read(`formatDelta(${value})`), '—');
  }
  assert.equal(h.read('formatScore(0)'), '0.00');
});

test('Vector preset remains independent after all current-page strategies are enabled', () => {
  const h = harness();
  h.read(`
    useMultiQuery.value = true; useHyde.value = true; useRagFusion.value = true;
    retrievalEmbedding.value = 'openai'; retrievalVectorstore.value = 'milvus';
    retrievalDomain.value = 'wind_oam'; retrievalDocType.value = 'fault_rule';
    vectorTopK.value = 99; runtimeMode.value = 'configured';
    evalExperimentPreset.value = 'vector_bge';
  `);
  const options = h.read('evaluationQueryOptions()');
  assert.equal(options.embedding, 'bge-large-zh');
  assert.equal(options.vectorstore, 'chroma');
  assert.equal(options.top_k, 20);
  assert.equal(options.runtime_mode, 'configured');
  for (const key of ['query_rewrite', 'query_expansion', 'query_transformation', 'metadata_router',
    'multi_query', 'hyde', 'rerank', 'compression', 'deduplicate', 'document_filter', 'parent_recovery', 'rag_fusion']) {
    assert.equal(options[key], false, key);
  }
  assert.equal(Object.keys(options.metadata_filter).length, 0);
  h.read("evalExperimentPreset.value = 'current'");
  assert.equal(h.read('evaluationQueryOptions().hyde'), true);
  assert.equal(h.read('evaluationQueryOptions().embedding'), 'openai');
});

test('query failure clears previous answer and never substitutes local chunks', async () => {
  let fail = false;
  const h = harness({ post: async () => {
    if (fail) throw new Error('adapter unavailable');
    return { original_query: 'first', retrieval_mode: 'vector', answer: 'old answer', options: { runtime_mode: 'offline' }, reranked_docs: [] };
  } });
  await h.read('runRetrieval()');
  assert.equal(h.read('retrievalTrace.value.answer'), 'old answer');
  h.read("state.value.chunks = [{chunk_id: 'demo'}]");
  fail = true;
  await h.read('runRetrieval()');
  assert.equal(h.read('retrievalTrace.value'), null);
  assert.equal(h.read('retrievalResult.value.items.length'), 0);
  assert.match(h.read('retrievalMessage.value'), /adapter unavailable/);
});

test('evaluation and graph failures clear previous results; process errors have their own message', async () => {
  const h = harness();
  h.read("evalSetPath.value = 'data/eval_sets/test.jsonl'; evalResult.value = {old:true}; runSamples.value = [{old:true}]");
  await h.read('runBatchEvaluation()');
  assert.equal(h.read('evalResult.value'), null);
  assert.equal(h.read('runSamples.value.length'), 0);
  h.read('graphResult.value = {old:true}');
  await h.read('runGraphQuery()');
  assert.equal(h.read('graphResult.value'), null);
  h.read("state.value.documents = [{document_id:'doc'}]; selectedDocumentId.value = 'doc'");
  await h.read('processSelectedDocument()');
  assert.match(h.read('processMessage.value'), /处理失败/);
  assert.equal(h.read('processingDocument.value'), false);
});

test('switching dataset ignores an older response and adopts the selected API path', async () => {
  const old = deferred();
  const h = harness({ get: (url) => url.endsWith('/old') ? old.promise : Promise.resolve({ dataset_id: 'new', eval_set: 'data/eval_sets/new.jsonl', samples: [] }) });
  const earlier = h.read("selectEvaluationDataset('old')");
  await h.read("selectEvaluationDataset('new')");
  old.resolve({ dataset_id: 'old', eval_set: 'data/eval_sets/old.jsonl' });
  await earlier;
  assert.equal(h.read('selectedDataset.value.dataset_id'), 'new');
  assert.equal(h.read('evalSetPath.value'), 'data/eval_sets/new.jsonl');
});

test('rapid run selection keeps samples and failures tied to the latest selected run', async () => {
  const old = deferred();
  const h = harness({ get: async (url) => {
    if (url.includes('/old')) await old.promise;
    const id = url.includes('/old') ? 'old' : 'new';
    return url.endsWith('/samples') || url.endsWith('/failures') ? { items: [{ run_id: id }] } : { run_id: id };
  } });
  const earlier = h.read("selectEvaluationRun('old')");
  await h.read("selectEvaluationRun('new')");
  old.resolve();
  await earlier;
  assert.equal(h.read('selectedRun.value.run_id'), 'new');
  assert.equal(h.read('runSamples.value[0].run_id'), 'new');
  assert.equal(h.read('runFailures.value[0].run_id'), 'new');
});

test('initial state finishes before evaluation loading and each hash navigation loads once', async () => {
  const stateResponse = deferred();
  const calls = [];
  const h = harness({ hash: '#/ragas', get: async (url) => {
    calls.push(url);
    if (url === '/api/state') return stateResponse.promise;
    if (url.endsWith('/summary')) return { metric_cards: [{ key: 'faithfulness', current_avg: 0.42 }] };
    return { items: [] };
  } });
  const loading = h.mount();
  assert.deepEqual(calls, ['/api/state']);
  stateResponse.resolve(createEmptyState());
  await loading;
  assert.equal(h.read('evaluation.value.metric_cards[0].current_avg'), 0.42);
  assert.equal(calls.filter((url) => url.endsWith('/summary')).length, 1);
  h.read("setPage('dashboard')");
  h.listeners.get('hashchange')();
  h.read("setPage('ragas')");
  h.listeners.get('hashchange')();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.filter((url) => url.endsWith('/summary')).length, 2);
});

test('API errors retain backend detail including validation locations', async () => {
  await assert.rejects(readApiResponse(new Response(JSON.stringify({ detail: 'configured adapter missing' }), { status: 503 })), /configured adapter missing/);
  await assert.rejects(readApiResponse(new Response(JSON.stringify({ detail: [{ loc: ['body', 'top_k'], msg: 'must be positive' }] }), { status: 422 })), /body.top_k: must be positive/);
});
