<script setup lang="ts">
import { computed } from 'vue'
import { extractEvidenceSteps, plainSummary } from '../utils/diagnosis'

const props = defineProps<{ events?: any[]; evidence?: unknown; running?: boolean }>()

const entries = computed(() => {
  if (props.events?.length) {
    return props.events
      .filter(event => event && (event.message || event.stage || event.current_step))
      .map((event, index) => ({
        title: String(event.current_step || event.stage || event.type || `Event ${index + 1}`),
        detail: plainSummary(event.message || event.result_preview || event.content, 260),
        status: event.type === 'error' ? 'error' : 'success',
        source: String(event.type || 'event').toUpperCase(),
      }))
  }
  return extractEvidenceSteps(props.evidence)
})
</script>

<template>
  <div v-if="entries.length" class="agent-trace">
    <article v-for="(entry,index) in entries" :key="`${entry.title}-${index}`" :class="entry.status">
      <div class="trace-rail"><span>{{index + 1}}</span><i></i></div>
      <div class="trace-content">
        <div><b>{{entry.title}}</b><em>{{entry.source}}</em></div>
        <p v-if="entry.detail">{{entry.detail}}</p>
      </div>
    </article>
    <div v-if="running" class="trace-running"><i></i> Waiting for the next Agent event…</div>
  </div>
  <el-empty v-else description="No Agent execution data returned" />
</template>
