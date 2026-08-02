import { defineStore } from 'pinia'
import { api, fetchAlerts, type AlertStatus } from '../api/client'

export const useAiopsStore = defineStore('aiops', {
  state: () => ({
    alerts: [] as any[],
    activeAlerts: [] as any[],
    summary: { total: 0, firing: 0, resolved: 0, diagnoses_today: 0 },
    loading: false,
  }),
  actions: {
    async refresh(status?: AlertStatus) {
      this.loading = true
      try {
        const [summary, alerts] = await Promise.all([
          api.get('/dashboard/summary', { params: { _: Date.now() } }), fetchAlerts(status),
        ])
        this.summary = summary.data.data
        this.alerts = alerts.data.data
      } catch {
        this.alerts = []
        this.summary = { total: 0, firing: 0, resolved: 0, diagnoses_today: 0 }
      } finally { this.loading = false }
    },
    async refreshDashboard() {
      this.loading = true
      try {
        const [summary, active, recent] = await Promise.all([
          api.get('/dashboard/summary', { params: { _: Date.now() } }),
          fetchAlerts('firing', 100),
          fetchAlerts(undefined, 100),
        ])
        this.activeAlerts = active.data.data.filter((alert: any) => alert.status === 'firing')
        this.alerts = recent.data.data
        this.summary = { ...summary.data.data, firing: this.activeAlerts.length }
      } catch {
        this.activeAlerts = []
        this.alerts = []
        this.summary = { total: 0, firing: 0, resolved: 0, diagnoses_today: 0 }
      } finally { this.loading = false }
    },
    async detail(id: string) {
      try { return (await api.get(`/alerts/history/${id}`)).data.data }
      catch { return null }
    },
  },
})
