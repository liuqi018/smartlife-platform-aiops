<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
const services=ref<any[]>([]),loading=ref(true)
onMounted(async()=>{try{
 services.value=(await api.get('/services/health',{params:{_:Date.now()}})).data.data
}catch{services.value=[]}finally{loading.value=false}})
</script>
<template><div><div class="page-intro"><div><h2>Service Health</h2><p>Current service state based on active alert lifecycles.</p></div></div>
<section class="panel health-api-list" v-loading="loading"><div class="health-api-head"><span>SERVICE</span><span>STATUS</span><span>ACTIVE ALERTS</span></div>
<div v-for="service in services" :key="service.service_name" class="health-api-row"><b>{{service.service_name}}</b><span :class="service.status.toLowerCase()"><i></i>{{service.status}}</span><strong>{{service.active_alert_count}}</strong></div>
<el-empty v-if="!loading&&!services.length" description="No service health data returned"/></section></div></template>
