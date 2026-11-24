// All calls to the FastAPI backend live here.
// VITE_API_URL is baked in at build time (no secrets here, only a public URL).
export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')

// Turn any backend error body into one readable sentence.
function readableError(status, body) {
  const detail = body?.detail
  if (status === 503) return 'All AI providers are unavailable right now. Please try again in a moment.'
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI 422 shape: [{ loc, msg, type }]
    return detail.map((d) => String(d.msg).replace(/^Value error, /, '')).join('; ')
  }
  if (typeof detail === 'string') return detail
  return `Request failed (HTTP ${status})`
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { 'Content-Type': 'application/json' }
  if (simulateFailure) headers['X-Simulate-Failure'] = simulateFailure

  const payload = { session_id: sessionId, message }
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt

  let response
  try {
    response = await fetch(`${API_URL}/chat`, { method: 'POST', headers, body: JSON.stringify(payload) })
  } catch {
    throw new Error(`Cannot reach the backend at ${API_URL}. Is it running?`)
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    // non-JSON body (should never happen with our backend)
  }
  if (!response.ok) {
    const err = new Error(readableError(response.status, body))
    err.status = response.status
    throw err
  }
  return body
}

export function newSessionId() {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(16).slice(2) + Date.now().toString(16)
  return `s-${random.replace(/-/g, '').slice(0, 12)}`
}
