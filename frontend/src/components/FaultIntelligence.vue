<script setup lang="ts">
defineProps<{ alerts:any[] }>()
const faults=[
 {name:'CPU High Usage',icon:'Cpu',match:'CPU',color:'red'},
 {name:'JVM OOM',icon:'Coin',match:'JVM',color:'orange'},
 {name:'MySQL Slow Query',icon:'DataLine',match:'Mysql',color:'purple'},
 {name:'Service Down',icon:'SwitchButton',match:'Service',color:'blue'},
]
const matches=(alerts:any[],key:string)=>alerts.filter(a=>String(a.alertname).toLowerCase().includes(key.toLowerCase()))
const time=(alerts:any[],key:string)=>matches(alerts,key)[0]?.startsAt ? new Date(matches(alerts,key)[0].startsAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}) : 'No trigger'
</script>
<template><div class="fault-grid"><article v-for="fault in faults" :key="fault.name" :class="fault.color"><div class="fault-icon"><el-icon><component :is="fault.icon"/></el-icon></div><div class="fault-name"><b>{{fault.name}}</b><span><i></i>Detection active</span></div><dl><div><dt>LAST TRIGGER</dt><dd>{{time(alerts,fault.match)}}</dd></div><div><dt>DIAGNOSES</dt><dd>{{matches(alerts,fault.match).length}}</dd></div></dl></article></div></template>
