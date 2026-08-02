<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const title = computed(() => String(route.meta.title || 'Operations Center'))
const menuGroups=[
  {label:'INCIDENT MANAGEMENT',items:[['Active Alerts','/alerts/active','Warning'],['Alert History','/alerts/history','Clock']]},
  {label:'DIAGNOSIS',items:[['Diagnosis Reports','/diagnosis/reports','Document'],['Execution Trace','/diagnosis/traces','Share']]},
  {label:'OBSERVABILITY',items:[['Metrics','/observability/metrics','DataLine'],['Service Health','/observability/services','Monitor']]},
  {label:'DIAGNOSIS KNOWLEDGE',items:[['Retrieved Knowledge','/knowledge/runbooks','Collection']]},
]
const groups = computed(() => {
  const paths = new Set<string>()
  return menuGroups.map(group => ({
    ...group,
    items: group.items.filter(item => {
      if (paths.has(item[1])) return false
      paths.add(item[1])
      return true
    }),
  })).filter(group => group.items.length)
})
</script>
<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">SL</div><div><b>SmartLife AIOps</b><small>OPERATIONS CENTER</small></div></div>
      <nav class="full-nav">
        <router-link to="/" class="nav-primary"><el-icon><Grid /></el-icon><span>Dashboard</span></router-link>
        <section v-for="group in groups" :key="group.label" class="nav-group">
          <h4>{{group.label}}</h4>
          <router-link v-for="item in group.items" :key="item[0]" :to="item[1]"><el-icon><component :is="item[2]"/></el-icon><span>{{item[0]}}</span></router-link>
        </section>
      </nav>
      <div class="platform-status"><i></i><div><b>Diagnosis Service</b><small>Operational</small></div></div>
    </aside>
    <main>
      <header><div><span class="eyebrow">SMARTLIFE AIOPS</span><h1>{{ title }}</h1></div><div class="header-actions"><span class="health"><i></i> SYSTEM HEALTHY</span><el-avatar :size="36">OP</el-avatar></div></header>
      <section class="content"><router-view /></section>
    </main>
  </div>
</template>
