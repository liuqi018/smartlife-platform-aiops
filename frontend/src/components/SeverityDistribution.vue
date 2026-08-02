<script setup lang="ts">
import { computed } from 'vue'
const props=defineProps<{ alerts:any[] }>()
const values=computed(()=>{
 const total=props.alerts.length||1
 const count=(s:string)=>props.alerts.filter(a=>(a.severity||'info').toLowerCase()===s).length
 return [{name:'Critical',value:count('critical'),color:'#ff566e'},{name:'Warning',value:count('warning'),color:'#f6a94a'},{name:'Info',value:Math.max(0,props.alerts.length-count('critical')-count('warning')),color:'#43a9ff'}].map(v=>({...v,pct:Math.round(v.value/total*100)}))
})
const gradient=computed(()=>{let pos=0;return `conic-gradient(${values.value.map(v=>{const start=pos;pos+=v.pct;return `${v.color} ${start}% ${pos}%`}).join(',')})`})
</script>
<template><div class="severity-layout"><div class="severity-donut" :style="{background:gradient}"><div><strong>{{alerts.length}}</strong><small>TOTAL</small></div></div><div class="severity-list"><div v-for="item in values" :key="item.name"><span><i :style="{background:item.color}"></i>{{item.name}}</span><b>{{item.value}}</b><small>{{item.pct}}%</small></div></div></div></template>
