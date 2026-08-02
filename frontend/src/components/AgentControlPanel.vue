<script setup lang="ts">
const workflow=[
  ['Bell','Alert Received','Alertmanager'],
  ['DataAnalysis','Metric Analysis','Prometheus'],
  ['Files','Evidence Collection','Metrics and context'],
  ['Reading','RAG Retrieval','Runbook knowledge'],
  ['Search','Root Cause Analysis','Evidence correlation'],
  ['DocumentChecked','Report Generated','Diagnosis report'],
]
</script>
<template><section class="diagnosis-timeline">
  <div class="timeline-status"><span><i></i>Service operational</span><b>Last completed 2 minutes ago</b></div>
  <div class="pipeline pipeline-vertical">
    <div v-for="step in workflow" :key="step[1]" class="pipeline-step">
      <div class="pipeline-index"><el-icon><Check /></el-icon></div>
      <el-icon><component :is="step[0]"/></el-icon>
      <div><b>{{step[1]}}</b><small>{{step[2]}}</small></div>
    </div>
  </div>
</section></template>
