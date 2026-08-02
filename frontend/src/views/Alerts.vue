<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAiopsStore } from '../stores/aiops'
import AlertTable from '../components/AlertTable.vue'
import type { AlertStatus } from '../api/client'
const store=useAiopsStore(); const route=useRoute(); const search=ref('')
const status=computed<AlertStatus>(()=>route.meta.status==='resolved'?'resolved':'firing')
const refresh=()=>store.refresh(status.value)
onMounted(refresh)
watch(()=>route.meta.status,refresh)
const rows=computed(()=>store.alerts.filter(a=>a.status===status.value&&(`${a.alertname} ${a.service} ${a.instance}`).toLowerCase().includes(search.value.toLowerCase())))
</script>
<template><div><div class="page-intro"><div><h2>{{status==='firing'?'Active Alerts':'Alert History'}}</h2><p>{{status==='firing'?'Currently firing alerts only.':'Resolved alert history.'}}</p></div><button class="primary-action" :disabled="store.loading" @click="refresh"><el-icon :class="{spin:store.loading}"><Refresh /></el-icon>Refresh</button></div>
<section class="panel"><div class="filterbar"><el-input v-model="search" placeholder="Search alert, service or instance..." :prefix-icon="'Search'" clearable /><span>{{rows.length}} incidents</span></div><AlertTable :alerts="rows" /></section></div></template>
