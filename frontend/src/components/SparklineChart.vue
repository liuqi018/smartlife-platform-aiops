<script setup lang="ts">
import { computed } from 'vue'
const props = withDefaults(defineProps<{ values: number[]; color?: string; height?: number }>(), {
  color: '#42b9ff', height: 48,
})
const points = computed(() => {
  const max = Math.max(...props.values, 1), min = Math.min(...props.values, 0)
  const range = max - min || 1
  return props.values.map((v, i) => `${(i/(props.values.length-1))*100},${props.height-5-((v-min)/range)*(props.height-12)}`).join(' ')
})
const area = computed(() => `0,${props.height} ${points.value} 100,${props.height}`)
</script>
<template>
  <svg class="sparkline-chart" viewBox="0 0 100 48" preserveAspectRatio="none" :style="{height:`${height}px`}">
    <defs><linearGradient :id="`fill-${color.replace('#','')}`" x1="0" y1="0" x2="0" y2="1"><stop offset="0" :stop-color="color" stop-opacity=".3"/><stop offset="1" :stop-color="color" stop-opacity="0"/></linearGradient></defs>
    <polygon :points="area" :fill="`url(#fill-${color.replace('#','')})`"/>
    <polyline :points="points" fill="none" :stroke="color" stroke-width="2" vector-effect="non-scaling-stroke"/>
  </svg>
</template>
