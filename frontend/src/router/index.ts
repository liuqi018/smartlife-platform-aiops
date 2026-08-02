import { createRouter, createWebHistory } from 'vue-router'
import ConsoleLayout from '../layouts/ConsoleLayout.vue'
import Dashboard from '../views/Dashboard.vue'
import Alerts from '../views/Alerts.vue'
import AlertDetail from '../views/AlertDetail.vue'
import DiagnosisReports from '../views/DiagnosisReports.vue'
import MetricsView from '../views/MetricsView.vue'
import ServiceHealthView from '../views/ServiceHealthView.vue'
import EvidenceView from '../views/EvidenceView.vue'
import ExecutionTrace from '../views/ExecutionTrace.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [{
    path: '/', component: ConsoleLayout, children: [
      { path: '', name: 'dashboard', component: Dashboard, meta:{title:'Operations Overview'} },
      { path: 'alerts', redirect:'/alerts/active' },
      { path: 'alerts/active', name: 'active-alerts', component: Alerts, meta:{title:'Active Alerts',status:'firing'} },
      { path: 'alerts/history', name: 'alert-history', component: Alerts, meta:{title:'Alert History',status:'resolved'} },
      { path: 'alerts/:id', name: 'alert-detail', component: AlertDetail, meta:{title:'Incident Details'} },
      { path: 'diagnosis/reports', name:'diagnosis-reports', component: DiagnosisReports, meta:{title:'Diagnosis Reports'} },
      { path: 'diagnosis/traces', name:'execution-traces', component: ExecutionTrace, meta:{title:'Execution Trace'} },
      { path: 'observability/metrics', name:'metrics', component: MetricsView, meta:{title:'Metrics'} },
      { path: 'observability/services', name:'service-health', component: ServiceHealthView, meta:{title:'Service Health'} },
      { path: 'knowledge/runbooks', name:'runbooks', component: EvidenceView, meta:{title:'Retrieved Knowledge',mode:'runbook'} },
    ],
  }],
})
