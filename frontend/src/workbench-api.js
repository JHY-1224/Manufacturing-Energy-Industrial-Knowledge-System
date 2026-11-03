const root = '/api/v2/workbench';
export async function request(path, { method = 'GET', body, signal, raw = false, timeout = method === 'GET' ? 15000 : 180000 } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(() => controller.abort(new Error('timeout')), timeout);
  let response;
  try {
    response = await fetch(root + path, { method, signal: controller.signal, headers: body && !raw ? { 'Content-Type': 'application/json' } : {}, body: body ? (raw ? body : JSON.stringify(body)) : undefined });
  if (!response.ok) {
    let error; try { error = await response.json(); } catch { error = {}; }
    const message = typeof error.detail === 'string' ? error.detail : error.error?.message || error.message;
    const traceId = error.trace_id || response.headers?.get?.('X-Trace-Id');
    const traceHint = typeof traceId === 'string' && traceId.length <= 120 ? `（Trace ${traceId}，可在质量与排错查看）` : '';
    const failure = new Error((message || (response.status >= 500 ? `本地服务暂时不可用（${response.status}），正在等待连接恢复` : `请求失败 (${response.status})`)) + traceHint);
    failure.traceId = traceId; failure.stage = error.stage;
    failure.status = response.status; throw failure;
  }
    return await response.json();
  } catch (error) {
    if (signal?.aborted) throw error;
    if (controller.signal.aborted) throw new Error('本地服务响应超时，请稍后重试');
    if (error instanceof TypeError) throw new Error('无法连接本地服务，请检查服务是否正在运行');
    throw error;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

// Decode split UTF-8 characters and split SSE frames, including CRLF servers.
export async function consumeEvents(stream, onEvent) {
  const reader = stream.getReader(), decoder = new TextDecoder(); let buffer = '', doneEvent = false;
  const flush = () => {
    let match;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, match.index); buffer = buffer.slice(match.index + match[0].length);
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
      if (!data) continue;
      const event = JSON.parse(data); onEvent(event);
      if (event.type === 'done' || event.type === 'error') doneEvent = true;
    }
  };
  try {
    while (true) { const result = await reader.read(); if (result.done) break; buffer += decoder.decode(result.value, { stream: true }); if (buffer.length > 4 * 1024 * 1024) throw new Error('事件过大'); flush(); }
    buffer += decoder.decode(); flush();
    if (!doneEvent) throw new Error('连接中断，回答尚未完成，请刷新会话查看状态');
  } finally { reader.releaseLock(); }
}

export async function streamTurn(session, body, signal, onEvent) {
  const response = await fetch(`${root}/sessions/${session}/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!response.ok) { const value = await response.json().catch(() => ({})); throw new Error(typeof value.detail === 'string' ? value.detail : value.error?.message || `会话请求失败 (${response.status})`); }
  return consumeEvents(response.body, onEvent);
}
