export type ServiceHealthStatus = 'Healthy' | 'Degraded' | 'Down'

export const serviceDefinitions = [
  { name: 'SmartLife Service', type: 'Application' },
  { name: 'MySQL', type: 'Database' },
  { name: 'Redis', type: 'Cache' },
  { name: 'JVM', type: 'Runtime' },
] as const

export function serviceForAlert(alertName: unknown): string | undefined {
  const name = String(alertName || '').toLowerCase()
  if (name === 'smartlifeservicedown') return 'SmartLife Service'
  if (name === 'mysqlunavailable' || name === 'smartlifemysqlslowqueryhigh') return 'MySQL'
  if (name === 'redisunavailable') return 'Redis'
  if (name.includes('jvm') || name.includes('heap') || name.includes('memory')) return 'JVM'
  return undefined
}

export function serviceStatus(service: string, alerts: any[]): ServiceHealthStatus {
  const names = new Set(alerts.map(alert => String(alert.alertname || '').toLowerCase()))
  if (service === 'SmartLife Service' && names.has('smartlifeservicedown')) return 'Down'
  if (service === 'Redis' && names.has('redisunavailable')) return 'Down'
  if (service === 'MySQL') {
    if (names.has('mysqlunavailable')) return 'Down'
    if (names.has('smartlifemysqlslowqueryhigh')) return 'Degraded'
  }
  return alerts.length ? 'Degraded' : 'Healthy'
}

export function activeForService(service: string, alerts: any[]): any[] {
  return alerts.filter(alert => alert.status === 'firing' && serviceForAlert(alert.alertname) === service)
}
