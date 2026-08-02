<script setup lang="ts">
import SparklineChart from './SparklineChart.vue'
defineProps<{
  label: string
  value: number | string
  tone: string
  icon: string
  note: string
  trend?: string
  values?: number[]
}>()
</script>
<template><article class="metric-card" :class="tone">
  <div class="metric-head"><span>{{ label }}</span><div class="metric-icon"><el-icon><component :is="icon" /></el-icon></div></div>
  <div class="metric-value"><strong>{{ value }}</strong><span :class="{negative:trend?.startsWith('-')}"><el-icon><TopRight /></el-icon>{{trend||'+0%'}}</span></div>
  <small>{{ note }}</small>
  <SparklineChart :values="values||[2,3,2,5,4,6,5,8,7,9]" :color="tone==='red'?'#ff566e':tone==='green'?'#32d39f':tone==='purple'?'#9b7cff':'#3caeff'" :height="44"/>
</article></template>
