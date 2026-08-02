<script setup lang="ts">
defineProps<{ status: string }>()
</script>
<template><span class="status-pill" :class="status"><i></i>{{ status.toUpperCase() }}</span></template>
