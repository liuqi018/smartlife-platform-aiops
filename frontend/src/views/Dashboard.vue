<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAiopsStore } from '../stores/aiops'
import BaseCard from '../components/BaseCard.vue'
import AgentControlPanel from '../components/AgentControlPanel.vue'
import RecentIncidentTable from '../components/RecentIncidentTable.vue'
import ActiveIncidentList from '../components/ActiveIncidentList.vue'
const store=useAiopsStore()
const lastRefresh=ref(new Date())
let timer:number
async function refresh(){await store.refreshDashboard();lastRefresh.value=new Date()}
onMounted(()=>{refresh();timer=window.setInterval(refresh,15000)})
onBeforeUnmount(()=>clearInterval(timer))
const recent=computed(()=>store.alerts.slice(0,6))
</script>
<template><div class="overview-page simplified-overview">
  <div class="hero-row overview-hero"><div><h2>SmartLife AIOps</h2><p>Incident monitoring and fault diagnosis</p></div><div class="system-strip"><div><i class="healthy"></i><span>System</span><b>Healthy</b></div><div><i class="agent"></i><span>Diagnosis service</span><b>Running</b></div><div><el-icon><Clock /></el-icon><span>Last update</span><b>{{lastRefresh.toLocaleTimeString('zh-CN',{hour12:false})}}</b></div><button class="refresh-btn icon-only" @click="refresh"><el-icon :class="{spin:store.loading}"><Refresh /></el-icon></button></div></div>
  <div class="overview-primary-grid">
    <BaseCard eyebrow="INCIDENT MANAGEMENT" title="Current Incidents" class="current-incidents"><template #action><span class="incident-count">{{store.activeAlerts.length}} active</span></template><ActiveIncidentList :alerts="store.activeAlerts"/></BaseCard>
    <BaseCard eyebrow="DIAGNOSIS SERVICE" title="Diagnosis Workflow" class="workflow-panel"><AgentControlPanel /></BaseCard>
  </div>
  <BaseCard eyebrow="INCIDENT MANAGEMENT" title="Recent Incidents" class="recent-panel"><template #action><router-link to="/alerts">View alert history <el-icon><ArrowRight /></el-icon></router-link></template><RecentIncidentTable :alerts="recent"/></BaseCard>
</div></template>
