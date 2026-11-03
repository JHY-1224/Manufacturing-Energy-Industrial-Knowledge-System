export async function readApiResponse(response) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => `${item.loc?.join('.') || 'request'}: ${item.msg || JSON.stringify(item)}`).join('; ')
        : detail ? JSON.stringify(detail) : `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return response.json();
}

export async function apiGet(path) {
  return readApiResponse(await fetch(path));
}

export async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readApiResponse(response);
}

export async function apiDelete(path) {
  const response = await fetch(path, { method: 'DELETE' });
  return readApiResponse(response);
}
