<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/client'
import { extractRunbooks } from '../utils/diagnosis'
const route=useRoute(),items=ref<any[]>([]),loading=ref(true)
const runbook=computed(()=>route.meta.mode==='runbook')
onMounted(async()=>{try{const alerts=(await api.get('/alerts/history?limit=30')).data.data;items.value=(await Promise.all(alerts.filter((a:any)=>a.diagnosis_status==='completed').slice(0,10).map((a:any)=>api.get(`/alerts/history/${a.id}`)))).map(r=>r.data.data)}catch{items.value=[]}finally{loading.value=false}})
const docs=(item:any)=>extractRunbooks(item?.evidence,item?.report_content||item?.report)
</script>
<template><div><div class="page-intro"><div><h2>{{runbook?'Retrieved Knowledge':'Execution Trace'}}</h2><p>{{runbook?'Runbook documents retrieved for completed diagnosis sessions.':'Persisted evidence steps from completed diagnoses.'}}</p></div></div>
<section v-loading="loading" class="evidence-page"><article v-for="item in items" :key="item.id" class="panel"><span class="panel-kicker">ALERT</span><h3>{{item.alertname}}</h3><p><b>Diagnosis session</b> · {{item.session_id||'Not available'}}</p><template v-if="runbook"><div v-if="docs(item).length"><span class="panel-kicker">RETRIEVED RUNBOOKS</span><div><span v-for="doc in docs(item)" :key="doc" class="doc-chip">{{doc}}</span></div></div><el-empty v-else description="No Runbook was retrieved for this diagnosis"/></template><ol v-else><li v-for="(step,i) in item.evidence" :key="i">{{Array.isArray(step)?step[0]:JSON.stringify(step)}}</li></ol></article></section></div></template>
