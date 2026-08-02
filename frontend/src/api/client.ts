import axios from 'axios'

export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE || '/api', timeout: 8000 })

export type AlertStatus = 'firing' | 'resolved'

export function fetchAlerts(status?: AlertStatus, limit = 100) {
  return api.get('/alerts/history', {
    params: { ...(status ? { status } : {}), limit, _: Date.now() },
    headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
  })
}

export async function streamDiagnosis(
  alert: Record<string, any>,
  onEvent: (event: any) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(`${import.meta.env.VITE_API_BASE || '/api'}/alerts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(alert),
    signal,
  })
  if (!response.ok || !response.body) throw new Error(`Diagnosis request failed: ${response.status}`)
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) {
    onEvent(await response.json())
    return
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split(/\r?\n\r?\n/)
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const line = chunk.split(/\r?\n/).find((item) => item.startsWith('data:'))
      if (!line) continue
      try { onEvent(JSON.parse(line.slice(5).trim())) } catch { /* heartbeat */ }
    }
  }
}
