<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
const metrics=ref<any[]>([]),loading=ref(true)
onMounted(async()=>{try{metrics.value=(await api.get('/metrics/current')).data.data}catch{metrics.value=[]}finally{loading.value=false}})
const value=(m:any)=>m.results?.[0]?.display_value||m.results?.[0]?.value||'Unavailable'
</script>
<template><div><div class="page-intro"><div><h2>Metrics</h2><p>Current indicators used by the diagnosis service.</p></div></div><div v-loading="loading" class="metric-focus-grid">
<section v-for="m in metrics" :key="m.name" class="panel metric-focus"><span>{{m.category}}</span><h3>{{m.name}}</h3><strong>{{value(m)}}</strong><code>{{m.promql}}</code><p :class="{error:m.error}">{{m.error||'Prometheus query successful'}}</p></section>
<el-empty v-if="!loading&&!metrics.length" description="No metrics returned"/></div></div></template>
