<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { marked } from 'marked'
import { api } from '../api/client'
import { extractConfidence, extractRunbooks } from '../utils/diagnosis'
const reports=ref<any[]>([]),loading=ref(true),detailLoading=ref(false),selected=ref<any>(),visible=ref(false)
const summary=(text:string)=>String(text||'').replace(/[#*`>|]/g,' ').replace(/\s+/g,' ').slice(0,110)
onMounted(async()=>{try{reports.value=(await api.get('/diagnosis/reports')).data.data}catch{reports.value=[]}finally{loading.value=false}})
const open=async(row:any)=>{visible.value=true;detailLoading.value=true;try{selected.value=(await api.get(`/diagnosis/reports/${row.id}`)).data.data}catch{selected.value=row}finally{detailLoading.value=false}}
const ragDocs=(row:any)=>extractRunbooks(row?.evidence,row?.report_content||row?.report)
const confidence=(row:any)=>extractConfidence(row?.report_content||row?.report,row?.confidence)
</script>
<template><div><div class="page-intro"><div><h2>Diagnosis Reports</h2><p>Completed fault analysis generated from alert evidence.</p></div></div>
<section class="panel report-list"><el-table v-loading="loading" :data="reports" @row-click="open">
  <el-table-column prop="alert_name" label="ALERT" min-width="180"/><el-table-column prop="service" label="SERVICE" width="150"/>
  <el-table-column prop="severity" label="SEVERITY" width="110"><template #default="{row}"><span class="severity" :class="row.severity">{{row.severity}}</span></template></el-table-column>
  <el-table-column label="ROOT CAUSE" min-width="280"><template #default="{row}">{{summary(row.root_cause||row.report)}}</template></el-table-column>
  <el-table-column label="CONFIDENCE" width="120"><template #default="{row}">{{confidence(row)}}</template></el-table-column>
  <el-table-column prop="created_at" label="CREATED AT" width="180"/><el-table-column label="ALERT STATUS" width="120"><template #default="{row}">{{row.alert_status||row.status}}</template></el-table-column>
</el-table></section>
<el-dialog v-model="visible" width="82%" title="Diagnosis Report"><div v-loading="detailLoading" class="report-detail">
  <div class="report-context"><div><span>ALERT</span><b>{{selected?.alert_name}}</b></div><div><span>SERVICE</span><b>{{selected?.service}}</b></div><div><span>SEVERITY</span><b>{{selected?.severity}}</b></div><div><span>STATUS</span><b>{{selected?.status}}</b></div></div>
  <el-tabs><el-tab-pane label="Full Report"><div class="markdown-body" v-html="marked.parse(selected?.report_content||selected?.report||'')"></div></el-tab-pane>
  <el-tab-pane label="Diagnosis Process"><ol class="detail-evidence"><li v-for="(step,i) in selected?.evidence||[]" :key="i"><b>Step {{i+1}}</b><span>{{Array.isArray(step)?step[0]:JSON.stringify(step)}}</span></li></ol></el-tab-pane>
  <el-tab-pane label="Prometheus Evidence"><pre class="evidence-raw">{{JSON.stringify(selected?.evidence||[],null,2)}}</pre></el-tab-pane>
  <el-tab-pane label="RAG Knowledge"><div class="rag-results"><span v-for="doc in ragDocs(selected)" :key="doc">{{doc}}</span><el-empty v-if="!ragDocs(selected).length" description="No runbook reference in this report"/></div></el-tab-pane></el-tabs>
</div><template #footer><router-link class="primary-action dialog-link" :to="`/alerts/${selected?.alert_id}`">Open incident</router-link></template></el-dialog></div></template>
