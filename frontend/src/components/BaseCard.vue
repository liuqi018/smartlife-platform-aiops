<script setup lang="ts">
withDefaults(defineProps<{ eyebrow?: string; title?: string; padded?: boolean }>(), {
  padded: true,
})
</script>
<template>
  <section class="ops-card" :class="{ 'ops-card--padded': padded }">
    <header v-if="eyebrow || title || $slots.action" class="ops-card__header">
      <div><span v-if="eyebrow" class="panel-kicker">{{ eyebrow }}</span><h3 v-if="title">{{ title }}</h3></div>
      <slot name="action" />
    </header>
    <slot />
  </section>
</template>
