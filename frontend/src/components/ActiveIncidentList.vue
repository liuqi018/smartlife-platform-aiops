<script setup lang="ts">
import { useRouter } from 'vue-router'
defineProps<{ alerts:any[] }>()
const router=useRouter()
const diagnosis=(row:any)=>row.diagnosis_status==='completed'?'Diagnosis report generated':row.diagnosis_status==='running'?'Analysis in progress':'Waiting for diagnosis'
</script>
<template><div class="active-incidents">
  <button v-for="row in alerts.filter(a=>a.status==='firing').slice(0,4)" :key="row.id" @click="router.push(`/alerts/${row.id}`)">
    <span class="incident-severity-bar" :class="row.severity"></span>
    <div class="active-incident-name"><b>{{row.alertname}}</b><small>INC-{{String(row.id).padStart(5,'0')}} · {{row.instance}}</small></div>
    <div><span class="field-label">SERVICE</span><b>{{row.service}}</b></div>
    <div><span class="field-label">SEVERITY</span><span class="severity-label" :class="row.severity">{{row.severity}}</span></div>
    <div><span class="field-label">STATUS</span><span class="firing-label">Firing</span></div>
    <div class="diagnosis-result"><span class="field-label">DIAGNOSIS RESULT</span><b><el-icon><CircleCheck v-if="row.diagnosis_status==='completed'"/><Loading v-else/></el-icon>{{diagnosis(row)}}</b></div>
    <el-icon class="row-arrow"><ArrowRight /></el-icon>
  </button>
  <div v-if="!alerts.some(a=>a.status==='firing')" class="no-incidents"><el-icon><CircleCheckFilled/></el-icon><b>No active incidents</b><span>All monitored services are operating normally.</span></div>
</div></template>
