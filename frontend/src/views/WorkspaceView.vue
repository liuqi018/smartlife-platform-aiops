<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route=useRoute()
const title=computed(()=>String(route.meta.title||'Operations Module'))
</script>
<template><div class="workspace-view"><div class="workspace-empty"><el-icon><Tools /></el-icon><h2>{{title}}</h2><p>This operations module is ready for its corresponding data integration.</p><router-link to="/">Return to Dashboard</router-link></div></div></template>
