<script setup lang="ts">
import { computed } from 'vue'
const firing = [2,3,2,4,3,5,4,7,6,8,5,6,9,7,8,6,5,7,4,5,3,4,2,3]
const resolved = [1,2,1,2,3,2,4,3,5,4,6,4,5,7,6,8,7,6,8,7,6,5,7,6]
const line = (values:number[]) => values.map((v,i)=>`${24+i*21},${178-v*15}`).join(' ')
const firingLine=computed(()=>line(firing)), resolvedLine=computed(()=>line(resolved))
</script>
<template><div class="trend-chart">
  <div class="chart-legend"><span class="firing"><i></i>Firing</span><span class="resolved"><i></i>Resolved</span><b>Last 24 hours</b></div>
  <svg viewBox="0 0 530 205" preserveAspectRatio="none">
    <g class="grid-lines"><line v-for="y in [28,68,108,148,188]" :key="y" x1="24" :y1="y" x2="510" :y2="y"/></g>
    <defs><linearGradient id="fireArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff556d" stop-opacity=".24"/><stop offset="1" stop-color="#ff556d" stop-opacity="0"/></linearGradient></defs>
    <polygon :points="`24,188 ${firingLine} 507,188`" fill="url(#fireArea)"/>
    <polyline :points="firingLine" fill="none" stroke="#ff5a72" stroke-width="2.2" vector-effect="non-scaling-stroke"/>
    <polyline :points="resolvedLine" fill="none" stroke="#37d39f" stroke-width="2.2" vector-effect="non-scaling-stroke"/>
  </svg>
  <div class="chart-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>NOW</span></div>
</div></template>
