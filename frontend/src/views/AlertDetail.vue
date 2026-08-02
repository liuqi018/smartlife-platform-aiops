<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import { streamDiagnosis } from '../api/client'
import { useAiopsStore } from '../stores/aiops'
import {
  extractConfidence, extractEvidenceSteps, extractMetrics, extractReportSection,
  extractRunbooks, plainSummary, toAlertmanagerPayload,
} from '../utils/diagnosis'
import StatusPill from '../components/StatusPill.vue'
import DiagnosisTimeline from '../components/DiagnosisTimeline.vue'

const route = useRoute()
const store = useAiopsStore()
const item = ref<any>({})
const running = ref(false)
const events = ref<any[]>([])
const controller = ref<AbortController>()

const reportSection = (section: number) => computed(() => extractReportSection(item.value.report, section))
const summarySection = reportSection(1)
const impactSection = reportSection(2)
const rootCauseSection = reportSection(5)
const recommendationSection = reportSection(6)
const metrics = computed(() => extractMetrics(item.value.evidence))
const evidenceSteps = computed(() => extractEvidenceSteps(item.value.evidence))
const runbooks = computed(() => extractRunbooks(item.value.evidence, item.value.report))
const confidence = computed(() => extractConfidence(item.value.report, item.value.confidence))
const diagnosisSummary = computed(() => plainSummary(summarySection.value, 420))
const markdown = (value: unknown) => marked.parse(String(value || ''))

function sparkHeight(points: number[], index: number): string {
  if (!points.length) return '10px'
  const values = points.slice(-14)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const value = values[index % values.length]
  const ratio = max === min ? 0.5 : (value - min) / (max - min)
  return `${10 + ratio * 30}px`
}

async function load() {
  item.value = await store.detail(String(route.params.id)) || {}
}

async function diagnose() {
  if (!item.value?.alertname || !item.value?.fingerprint) {
    ElMessage.error('The alert context is incomplete and diagnosis cannot start.')
    return
  }
  running.value = true
  events.value = []
  controller.value = new AbortController()
  try {
    await streamDiagnosis(toAlertmanagerPayload(item.value), event => {
      events.value.push(event)
      if (event.report) item.value.report = event.report
      if (event.diagnosis?.report) item.value.report = event.diagnosis.report
      if (event.evidence) item.value.evidence = event.evidence
    }, controller.value.signal)
    await load()
  } catch (error: any) {
    if (error?.name !== 'AbortError') ElMessage.error(error?.message || 'Diagnosis failed')
  } finally {
    running.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => controller.value?.abort())
</script>

<template>
  <div v-if="item.id" class="aiops-detail">
    <div class="detail-top">
      <router-link to="/alerts" class="back"><el-icon><ArrowLeft /></el-icon> Alert Center</router-link>
      <div class="incident-title">
        <div><span class="incident-id">INC-{{String(item.id).padStart(5,'0')}}</span><h2>{{item.alertname}}</h2><p>{{item.service}} · {{item.instance}}</p></div>
        <div class="title-actions"><StatusPill :status="item.status"/><button class="primary-action" :disabled="running" @click="diagnose"><el-icon><Cpu /></el-icon>{{running ? 'Agent running…' : 'Run AI diagnosis'}}</button></div>
      </div>
    </div>

    <section class="fault-overview">
      <div class="fault-signal" :class="item.severity"><el-icon><WarningFilled /></el-icon></div>
      <div class="fault-heading"><span class="panel-kicker">FAULT SUMMARY</span><h3>{{item.alertname}}</h3><p v-if="diagnosisSummary">{{diagnosisSummary}}</p><p v-else>No diagnosis summary has been returned.</p></div>
      <dl><div><dt>Severity</dt><dd>{{item.severity}}</dd></div><div><dt>State</dt><dd>{{item.status}}</dd></div><div><dt>Started</dt><dd>{{item.startsAt}}</dd></div><div><dt>Ended</dt><dd>{{item.endsAt || 'Still firing'}}</dd></div></dl>
    </section>

    <div class="diagnosis-summary-grid">
      <section class="panel diagnosis-summary"><div class="panel-head"><div><span class="panel-kicker">DIAGNOSIS SUMMARY</span><h3>Agent assessment</h3></div><span class="confidence"><b>{{confidence}}</b></span></div><div v-if="summarySection" class="section-markdown" v-html="markdown(summarySection)"></div><el-empty v-else description="No diagnosis report returned"/></section>
      <section class="panel impact-summary"><span class="panel-kicker">IMPACT ANALYSIS</span><h3>Observed impact</h3><div v-if="impactSection" class="section-markdown" v-html="markdown(impactSection)"></div><el-empty v-else description="No impact analysis returned"/></section>
    </div>

    <section class="panel trace-panel"><div class="panel-head"><div><span class="panel-kicker">AGENT EXECUTION TRACE</span><h3>Diagnostic execution</h3></div><span class="live-badge" :class="{active:running}"><i></i>{{running ? 'LIVE' : item.diagnosis_status}}</span></div><DiagnosisTimeline :events="events" :evidence="item.evidence" :running="running"/></section>

    <section class="panel evidence-chain"><div class="panel-head"><div><span class="panel-kicker">EVIDENCE CHAIN</span><h3>Correlated diagnostic evidence</h3></div><span class="source-tag">{{evidenceSteps.length}} STEPS · {{metrics.length}} METRICS</span></div>
      <div v-if="evidenceSteps.length" class="evidence-chain-flow"><article v-for="(step,index) in evidenceSteps" :key="index" :class="step.status"><span class="chain-index">{{index+1}}</span><div><em>{{step.source}}</em><b>{{step.title}}</b><p v-if="step.detail">{{step.detail}}</p></div><el-icon><CircleCheck v-if="step.status==='success'"/><CircleClose v-else/></el-icon></article></div>
      <el-empty v-else description="No evidence chain returned"/>
    </section>

    <div class="detail-grid evidence-assets">
      <section class="panel span2"><div class="panel-head"><div><span class="panel-kicker">PROMETHEUS EVIDENCE</span><h3>Metric observations</h3></div><span class="source-tag">PROMQL</span></div><div v-if="metrics.length" class="evidence-grid"><article v-for="m in metrics" :key="m.name" class="evidence-card" :class="m.level"><div><span>{{m.group}}</span><small>{{m.name}}</small></div><strong>{{m.display}}</strong><p><i></i>{{m.judgement}}</p><div class="spark"><i v-for="n in 14" :key="n" :style="{height:sparkHeight(m.points,n-1)}"></i></div></article></div><el-empty v-else description="No Prometheus metric evidence returned"/></section>
      <section class="panel runbook"><span class="panel-kicker">RAG KNOWLEDGE</span><h3>Matched runbooks</h3><article v-for="name in runbooks" :key="name"><div class="doc-icon"><el-icon><Document /></el-icon></div><div><b>{{name}}</b><p>Referenced in the returned evidence or report.</p></div></article><el-empty v-if="!runbooks.length" description="No runbook reference returned"/></section>
    </div>

    <div class="analysis-grid">
      <section class="panel root-cause-panel"><div class="analysis-title"><el-icon><Aim /></el-icon><div><span class="panel-kicker">ROOT CAUSE ANALYSIS</span><h3>Evidence-backed conclusion</h3></div></div><div v-if="rootCauseSection" class="section-markdown" v-html="markdown(rootCauseSection)"></div><el-empty v-else description="No root cause analysis returned"/></section>
      <section class="panel repair-panel"><div class="analysis-title"><el-icon><Tools /></el-icon><div><span class="panel-kicker">REMEDIATION</span><h3>Repair recommendations</h3></div></div><div v-if="recommendationSection" class="section-markdown" v-html="markdown(recommendationSection)"></div><el-empty v-else description="No repair recommendations returned"/></section>
    </div>

    <el-collapse class="full-report"><el-collapse-item title="View complete Markdown diagnosis report" name="report"><div class="markdown-body" v-html="markdown(item.report)"></div></el-collapse-item></el-collapse>
  </div>
  <el-skeleton v-else :rows="12" animated />
</template>
