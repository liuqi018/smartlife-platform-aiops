<script setup lang="ts">
import dayjs from 'dayjs'
import { useRouter } from 'vue-router'
import StatusPill from './StatusPill.vue'
import { formatAlertDuration } from '../utils/duration'
defineProps<{ alerts:any[]; compact?:boolean }>()
const router = useRouter()
const fmt = (v:string) => v ? dayjs(v).format('MM-DD HH:mm:ss') : '—'
const duration = (row:any) => formatAlertDuration(row.startsAt, row.endsAt)
const openAlert = (row: any) => router.push(`/alerts/${row.id}`)
</script>
<template><div class="table-wrap"><el-table :data="alerts" @row-click="openAlert">
  <el-table-column label="ALERT" min-width="210"><template #default="{row}"><div class="alert-name"><span :class="row.severity"></span><div><b>{{row.alertname}}</b><small>{{row.service}}</small></div></div></template></el-table-column>
  <el-table-column prop="severity" label="SEVERITY" width="120"><template #default="{row}"><span class="severity" :class="row.severity">{{row.severity}}</span></template></el-table-column>
  <el-table-column v-if="!compact" prop="instance" label="INSTANCE" min-width="165" />
  <el-table-column label="STATUS" width="125"><template #default="{row}"><StatusPill :status="row.status" /></template></el-table-column>
  <el-table-column label="STARTED AT" width="150"><template #default="{row}">{{fmt(row.startsAt)}}</template></el-table-column>
  <el-table-column v-if="!compact" label="ENDED AT" width="150"><template #default="{row}">{{fmt(row.endsAt)}}</template></el-table-column>
  <el-table-column v-if="!compact" label="DURATION" width="110"><template #default="{row}">{{duration(row)}}</template></el-table-column>
  <el-table-column label="" width="48"><template #default><el-icon><ArrowRight /></el-icon></template></el-table-column>
</el-table></div></template>
