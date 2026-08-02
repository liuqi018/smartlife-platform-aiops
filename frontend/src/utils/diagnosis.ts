export type AlertContext = {
  id?: number
  alertname: string
  fingerprint: string
  severity?: string
  service?: string
  instance?: string
  status?: string
  startsAt?: string
  endsAt?: string | null
}

export type MetricEvidence = {
  group: string
  name: string
  display: string
  level: 'critical' | 'warning' | 'normal'
  judgement: string
  points: number[]
}

export type EvidenceStep = {
  title: string
  source: string
  detail: string
  status: 'success' | 'error'
}

function decoded(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const text = value.trim()
  if (!(text.startsWith('{') || text.startsWith('['))) return value
  try { return JSON.parse(text) } catch { return value }
}

function walk(value: unknown, visit: (item: Record<string, any>) => void): void {
  const item = decoded(value)
  if (Array.isArray(item)) {
    item.forEach(child => walk(child, visit))
    return
  }
  if (!item || typeof item !== 'object') return
  const record = item as Record<string, any>
  visit(record)
  Object.values(record).forEach(child => walk(child, visit))
}

function groupFor(name: string): string {
  const lower = name.toLowerCase()
  if (lower.includes('jvm') || lower.includes('heap')) return 'JVM'
  if (lower.includes('mysql')) return 'MYSQL'
  if (lower.includes('redis')) return 'REDIS'
  if (lower === 'up' || lower.includes('probe_success')) return 'AVAILABILITY'
  if (lower.includes('cpu')) return 'CPU'
  return 'PROMETHEUS'
}

export function extractMetrics(evidence: unknown): MetricEvidence[] {
  const found = new Map<string, MetricEvidence>()
  walk(evidence, item => {
    const name = String(item.metric_name || item.metric || '').trim()
    if (!name || (!('value' in item) && !item.summary && !Array.isArray(item.values))) return
    const values = Array.isArray(item.values)
      ? item.values.map((point: any) => Number(point?.value)).filter(Number.isFinite)
      : []
    const rawValue = item.value ?? item.summary?.last_value ?? values[values.length - 1]
    const display = String(
      item.display_value ?? item.summary?.last_display_value ?? rawValue ?? '',
    )
    if (!display) return
    const numeric = Number(rawValue)
    const availability = name === 'up' || name.includes('probe_success')
    const level = availability && numeric === 0
      ? 'critical'
      : /critical|failure|unavailable/i.test(String(item.description || ''))
        ? 'critical'
        : 'normal'
    const labels = item.labels || item.normalized_labels || {}
    const labelText = Object.entries(labels)
      .filter(([key]) => ['job', 'instance'].includes(key))
      .map(([key, value]) => `${key}=${value}`)
      .join(', ')
    found.set(`${name}:${labelText}`, {
      group: groupFor(name),
      name: labelText ? `${name}{${labelText}}` : name,
      display,
      level,
      judgement: String(item.description || (availability ? 'Health check metric' : 'Prometheus evidence')),
      points: values.length ? values : (Number.isFinite(numeric) ? [numeric] : []),
    })
  })
  return [...found.values()]
}

function legacyExtractRunbooksRemoved(evidence: unknown, report: unknown): string[] {
  const text = `${JSON.stringify(evidence || [])}\n${String(report || '')}`
  const matches = text.match(/[^\s"'`|<>\\/：:，,。；;（）()\[\]]+[^\s"'`|<>\\/]*\.md/giu) || []
  return [...new Set(matches.map(name => name.replace(/^[-*]+/, '').trim()))]
}

function markdownReferences(value: unknown): string[] {
  const texts: string[] = []
  const collect = (value: unknown): void => {
    const item = decoded(value)
    if (typeof item === 'string') texts.push(item)
    else if (Array.isArray(item)) item.forEach(collect)
    else if (item && typeof item === 'object') Object.values(item).forEach(collect)
  }
  collect(value)

  const found = new Set<string>()
  texts.forEach(value => {
    const text = value.replace(/\\[rn]/g, '\n')
    const matches = text.match(/[^\\/\r\n"'`|<>\[\]{}(),，；;]*\.md/giu) || []
    matches.forEach(raw => {
      const name = raw
        .split(/[\\/]/).pop()!
        .replace(/^.*?(?:来源|source)\s*[:：=]\s*/iu, '')
        .replace(/^[\s:：=\-*•]+/u, '')
        .trim()
      if (name && name.toLowerCase().endsWith('.md')) found.add(name)
    })
  })
  const normalized = new Map<string, string>()
  found.forEach(raw => {
    const name = raw
      .replace(/^.*(?:\u6765\u6e90|source|\u547d\u4e2d\u6587\u6863|\u68c0\u7d22\u8303\u56f4)\s*[:\uff1a=]\s*/iu, '')
      .replace(/^.*[:\uff1a=]\s*/u, '')
      .replace(/^[\s\-*\u2022]+/u, '')
      .trim()
    const key = name.normalize('NFKC').replace(/\s+/g, ' ').toLocaleLowerCase()
    if (name && !normalized.has(key)) normalized.set(key, name)
  })
  return [...normalized.values()]
}

export function extractRunbooks(evidence: unknown, report: unknown): string[] {
  const retrieved = markdownReferences(evidence)
  return retrieved.length ? retrieved : markdownReferences(report)
}

export function extractReportSection(report: unknown, section: number): string {
  const text = String(report || '').replace(/\r\n/g, '\n')
  if (!text) return ''
  const heading = new RegExp(`^##\\s+${section}(?:\\.|、)?[^\\n]*$`, 'm')
  const match = heading.exec(text)
  if (!match) return ''
  const start = match.index + match[0].length
  const rest = text.slice(start)
  const next = /^##\s+\d+(?:\.|、)?[^\n]*$/m.exec(rest)
  return rest.slice(0, next?.index ?? rest.length).trim()
}

export function plainSummary(markdown: unknown, limit = 260): string {
  return String(markdown || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[#>*`|_[\]()-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit)
}

export function extractEvidenceSteps(evidence: unknown): EvidenceStep[] {
  if (!Array.isArray(evidence)) return []
  return evidence.map((step: any, index) => {
    const task = String(Array.isArray(step) ? step[0] || '' : step?.task || step?.step || '')
    const result = String(Array.isArray(step) ? step[1] || '' : step?.result || step?.output || '')
    const combined = `${task} ${result}`.toLowerCase()
    const source = /retrieve_knowledge|runbook|\.md/.test(combined)
      ? 'RAG'
      : /prometheus|promql|metric|query_prometheus/.test(combined)
        ? 'Prometheus'
        : /jvm|thread|heap|gc/.test(combined)
          ? 'JVM'
          : /log|loki|elasticsearch/.test(combined) ? 'Logs' : 'Agent'
    const failed = /error|failed|failure|执行失败|查询失败/.test(combined)
    return {
      title: task || `Evidence ${index + 1}`,
      source,
      detail: plainSummary(result, 360),
      status: failed ? 'error' : 'success',
    }
  })
}

export function extractConfidence(report: unknown, numeric?: unknown): string {
  if (numeric !== null && numeric !== undefined && numeric !== '') return `${numeric}%`
  const text = String(report || '')
  const scoped = text.match(/(?:可信度|置信度)[\s\S]{0,100}?(?:\*\*)?\s*(高|中|低)(?:等|度)?(?:\*\*)?/i)
  if (scoped) return `${scoped[1]}可信度`
  const plain = text.match(/(?:confidence|可信度|置信度)\s*[：:]?\s*(high|medium|low|高|中|低)/i)
  if (!plain) return '未标注'
  const labels: Record<string, string> = { high: '高', medium: '中', low: '低' }
  return `${labels[plain[1].toLowerCase()] || plain[1]}可信度`
}

export function toAlertmanagerPayload(alert: AlertContext) {
  const status = alert.status === 'resolved' ? 'resolved' : 'firing'
  return {
    version: '4',
    status,
    receiver: 'aiops-agent',
    groupLabels: { alertname: alert.alertname },
    commonLabels: {
      alertname: alert.alertname,
      alert_id: String(alert.id ?? ''),
      severity: alert.severity || 'warning',
      service: alert.service || '',
      instance: alert.instance || '',
    },
    commonAnnotations: { summary: `Manual diagnosis for alert ${alert.id ?? ''}`.trim() },
    externalURL: window.location.origin,
    alerts: [{
      status,
      labels: {
        alertname: alert.alertname,
        alert_id: String(alert.id ?? ''),
        severity: alert.severity || 'warning',
        service: alert.service || '',
        instance: alert.instance || '',
      },
      annotations: {
        summary: `Manual diagnosis for ${alert.alertname}`,
        description: `Triggered from alert history id=${alert.id ?? ''}`,
      },
      startsAt: alert.startsAt || new Date().toISOString(),
      endsAt: alert.endsAt || '',
      fingerprint: alert.fingerprint,
    }],
  }
}
