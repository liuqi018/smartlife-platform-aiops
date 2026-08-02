<script setup lang="ts">
import { useRouter } from 'vue-router'
const props=defineProps<{ alerts:any[] }>()
const router=useRouter()
const duration=(row:any)=>{
 const start=new Date(row.startsAt).getTime(), end=row.endsAt?new Date(row.endsAt).getTime():Date.now()
 const mins=Math.max(1,Math.round((end-start)/60000))
 return mins>59?`${Math.floor(mins/60)}h ${mins%60}m`:`${mins}m`
}
const rootCause=(row:any)=>{
 const text=`${row.alertname} ${row.service}`.toLowerCase()
 if(text.includes('cpu'))return 'High process CPU utilization'
 if(text.includes('jvm')||text.includes('heap'))return 'JVM heap pressure'
 if(text.includes('mysqlunavailable'))return 'MySQL health check failure'
 if(text.includes('redisunavailable'))return 'Redis health check failure'
 if(text.includes('mysql')||text.includes('slow'))return 'Slow database query'
 if(text.includes('smartlifeservicedown'))return 'Service health check failure'
 return row.diagnosis_status==='completed'?'Diagnosis report available':'Analysis in progress'
}
</script>
<template><div class="incident-list">
  <div class="incident-list__head"><span>INCIDENT ID</span><span>SERVICE</span><span>SEVERITY</span><span>DURATION</span><span>ROOT CAUSE</span><span>STATUS</span></div>
  <button v-for="row in props.alerts.slice(0,6)" :key="row.id" @click="router.push(`/alerts/${row.id}`)">
    <span class="incident-link">INC-{{String(row.id).padStart(5,'0')}}</span>
    <span><b>{{row.service}}</b><small>{{row.alertname}}</small></span>
    <span><i class="severity-dot" :class="row.severity"></i>{{row.severity}}</span>
    <span>{{duration(row)}}</span><span class="cause">{{rootCause(row)}}</span>
    <span class="incident-status" :class="row.status">{{row.status}}</span>
  </button>
</div></template>
