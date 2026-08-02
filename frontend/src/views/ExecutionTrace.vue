<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
const traces=ref<any[]>([]),selectedId=ref<number>(),trace=ref<any>(),loading=ref(true),detailLoading=ref(false)
async function loadTrace(id:number){selectedId.value=id;detailLoading.value=true;try{trace.value=(await api.get(`/diagnosis/traces/${id}`)).data.data}finally{detailLoading.value=false}}
onMounted(async()=>{try{traces.value=(await api.get('/diagnosis/traces')).data.data;if(traces.value.length)await loadTrace(traces.value[0].id)}finally{loading.value=false}})
</script>
<template><div><div class="page-intro"><div><h2>Execution Trace</h2><p>LangGraph diagnosis steps reconstructed from persisted workflow evidence.</p></div></div>
<div class="trace-layout" v-loading="loading"><aside class="panel trace-sessions"><button v-for="item in traces" :key="item.id" :class="{active:selectedId===item.id}" @click="loadTrace(item.id)"><b>{{item.alert_name}}</b><span>{{item.service}}</span><small>{{item.created_at}}</small></button><el-empty v-if="!traces.length" description="No execution traces"/></aside>
<section class="panel trace-detail" v-loading="detailLoading"><template v-if="trace"><div class="trace-header"><div><span>ALERT NAME</span><b>{{trace.alert_name}}</b></div><div><span>SERVICE</span><b>{{trace.service}}</b></div><div><span>STATUS</span><b class="completed">{{trace.status}}</b></div></div>
<div class="trace-timeline"><article v-for="(step,i) in trace.steps" :key="i" :class="step.status"><div class="trace-marker">{{i+1}}</div><div><span>STEP {{i+1}}</span><h3>{{step.stage}}</h3><p>{{step.message}}</p></div><div class="trace-meta"><b>{{step.status}}</b><span>{{step.duration==null?'—':step.duration}}</span></div></article></div></template><el-empty v-else description="Select a diagnosis session"/></section></div></div></template>
