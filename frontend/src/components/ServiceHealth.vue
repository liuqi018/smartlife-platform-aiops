<script setup lang="ts">
import { activeForService, serviceDefinitions, serviceStatus } from '../utils/serviceHealth'
const props=defineProps<{ alerts:any[] }>()
const services=serviceDefinitions
const related=(service:typeof services[number])=>activeForService(service.name,props.alerts)
const state=(service:typeof services[number])=>serviceStatus(service.name,related(service))
</script>
<template><div class="service-health">
  <div v-for="service in services" :key="service.name" class="service-row">
    <span class="service-indicator" :class="state(service).toLowerCase()"></span>
    <div><b>{{service.name}}</b><small>{{service.type}}</small></div>
    <span class="health-state" :class="state(service).toLowerCase()">{{state(service)}}</span>
    <div class="availability"><b>{{related(service).length}}</b><small>ACTIVE INCIDENTS</small></div>
    <span class="check-count">{{related(service).length}} active</span>
  </div>
</div></template>
