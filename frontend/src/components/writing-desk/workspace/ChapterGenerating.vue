<!-- AIMETA P=生成中_章节生成进度|R=进度展示_流式输出|NR=不含生成逻辑|E=component:ChapterGenerating|X=internal|A=生成状态|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="h-full flex items-center justify-center">
    <div class="md-card md-card-outlined p-8 text-center max-w-md" style="border-radius: var(--md-radius-xl);">
      <div class="w-16 h-16 rounded-full mx-auto flex items-center justify-center mb-5" style="background-color: var(--md-primary-container);">
        <div class="md-spinner" style="width: 36px; height: 36px;"></div>
      </div>
      <h3 class="md-headline-small font-semibold mb-3">{{ statusText.title }}</h3>
      <div class="space-y-2 md-body-medium md-on-surface-variant mb-6">
        <p class="m3-pulse">{{ statusText.line1 }}</p>
        <p class="m3-pulse" style="animation-delay: 0.5s">{{ statusText.line2 }}</p>
        <p class="m3-pulse" style="animation-delay: 1s">🎨 描绘生动场景...</p>
      </div>
      <div class="md-progress-linear mb-2" role="progressbar" :aria-valuemin="0" :aria-valuemax="100" :aria-valuenow="Math.round(progressPercent)">
        <div class="md-progress-linear-bar" :style="{ width: `${progressPercent}%` }"></div>
      </div>
      <div class="flex items-center justify-between mb-5">
        <span class="md-label-small md-on-surface-variant">当前阶段：{{ stageLabel }}<template v-if="stepIndexText">（{{ stepIndexText }}）</template></span>
        <span class="md-label-small md-on-surface-variant">{{ Math.round(progressPercent) }}%</span>
      </div>
      <div class="md-card md-card-filled p-4 text-left" style="border-radius: var(--md-radius-lg);">
        <p class="md-body-small md-on-surface-variant">
          {{ etaText }}，已耗时 {{ elapsedText }}。您可以随时离开此页面，生成完成后再回来查看。
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { Chapter } from '@/api/novel'

interface Props {
  chapterNumber: number | null
  status: Chapter['generation_status'] | null
  generationProgress?: number | null
  generationStep?: string | null
  generationStepIndex?: number | null
  generationStepTotal?: number | null
  generationStartedAt?: string | null
  statusUpdatedAt?: string | null
}

const props = defineProps<Props>()

const clockNow = ref(Date.now())
const localStartAt = ref(Date.now())
let timer: number | null = null

const STAGE_CONFIG: Record<'generating' | 'evaluating' | 'selecting', { start: number; end: number; expectedSeconds: number; label: string }> = {
  generating: { start: 8, end: 78, expectedSeconds: 150, label: '正文生成' },
  evaluating: { start: 78, end: 94, expectedSeconds: 40, label: '版本评审' },
  selecting: { start: 94, end: 99, expectedSeconds: 20, label: '结果收敛' },
}

const STEP_LABELS: Record<string, string> = {
  context_prep: '准备上下文',
  director_mission: '导演脚本',
  rag_retrieval: '检索上下文',
  draft_generation: '生成正文',
  quality_review: '质量评审',
  persist_versions: '保存版本',
  waiting_for_confirm: '等待确认',
  selecting_version: '确认版本',
  evaluating: '评审中',
  evaluation_done: '评审完成',
  completed: '已完成',
  failed: '失败',
  evaluation_failed: '评审失败',
}

const clampPercent = (value: number): number => Math.max(0, Math.min(100, value))

const parseBackendTimestampToMs = (raw?: string | null): number | null => {
  if (!raw) return null
  const normalized = raw.trim()
  if (!normalized) return null

  // 后端返回的 DATETIME 在 SQLite 场景下可能不带时区，
  // 这里统一按北京时间（UTC+08:00）解析，避免被浏览器按其他时区解释。
  const hasExplicitTimezone = /([zZ]|[+\-]\d{2}:\d{2})$/.test(normalized)
  const isoCandidate = normalized.includes('T')
    ? normalized
    : normalized.replace(' ', 'T')
  const parseTarget = hasExplicitTimezone ? isoCandidate : `${isoCandidate}+08:00`
  const ts = Date.parse(parseTarget)
  return Number.isFinite(ts) ? ts : null
}

const parsedGenerationStartedAt = computed(() => {
  return parseBackendTimestampToMs(props.generationStartedAt)
})

const parsedStatusUpdatedAt = computed(() => {
  return parseBackendTimestampToMs(props.statusUpdatedAt)
})

const startTimestamp = computed(() => parsedGenerationStartedAt.value ?? parsedStatusUpdatedAt.value ?? localStartAt.value)

const elapsedSeconds = computed(() => {
  const delta = Math.floor((clockNow.value - startTimestamp.value) / 1000)
  return Math.max(0, delta)
})

const backendProgress = computed(() => {
  if (props.generationProgress === null || props.generationProgress === undefined) return null
  if (!Number.isFinite(props.generationProgress)) return null
  if (!(props.status === 'generating' || props.status === 'evaluating' || props.status === 'selecting')) return null
  return clampPercent(props.generationProgress)
})

const currentStageConfig = computed(() => {
  if (props.status === 'generating' || props.status === 'evaluating' || props.status === 'selecting') {
    return STAGE_CONFIG[props.status]
  }
  return null
})

const progressPercent = computed(() => {
  if (backendProgress.value !== null) {
    return backendProgress.value
  }
  const config = currentStageConfig.value
  if (!config) return 15
  const span = config.end - config.start
  const ratio = Math.min(elapsedSeconds.value / config.expectedSeconds, 0.98)
  return config.start + span * ratio
})

const stageLabel = computed(() => {
  if (props.generationStep && STEP_LABELS[props.generationStep]) {
    return STEP_LABELS[props.generationStep]
  }
  return currentStageConfig.value?.label ?? '处理中'
})

const stepIndexText = computed(() => {
  const index = props.generationStepIndex ?? null
  const total = props.generationStepTotal ?? null
  if (!index || !total || index <= 0 || total <= 0) return ''
  return `步骤 ${index}/${total}`
})

const etaText = computed(() => {
  if (backendProgress.value !== null) {
    if (backendProgress.value >= 99) return '预计即将完成'
    if (elapsedSeconds.value > 5 && backendProgress.value >= 3) {
      const estimatedTotal = Math.ceil((elapsedSeconds.value * 100) / backendProgress.value)
      const remain = Math.max(0, estimatedTotal - elapsedSeconds.value)
      if (remain <= 0) return '预计即将完成'
      if (remain < 60) return '预计剩余不足1分钟'
      return `预计剩余约${Math.ceil(remain / 60)}分钟`
    }
  }

  const config = currentStageConfig.value
  if (!config) return '正在处理请求'
  const remain = config.expectedSeconds - elapsedSeconds.value
  if (remain <= 0) return '即将进入下一步'
  if (remain < 60) return '预计剩余不足1分钟'
  const minutes = Math.ceil(remain / 60)
  return `预计剩余约${minutes}分钟`
})

const elapsedText = computed(() => {
  const total = elapsedSeconds.value
  const mins = Math.floor(total / 60)
  const secs = total % 60
  return `${mins}分${String(secs).padStart(2, '0')}秒`
})

const statusText = computed(() => {
  switch (props.status) {
    case 'generating':
      return {
        title: `AI 正在为您创作第${props.chapterNumber}章`,
        line1: '✨ 构思情节发展...',
        line2: '📝 编织精彩对话...'
      }
    case 'evaluating':
      return {
        title: `AI 正在评审第${props.chapterNumber}章的多个版本`,
        line1: '🧐 分析故事结构...',
        line2: '⚖️ 比较版本优劣...'
      }
    case 'selecting':
      return {
        title: `正在确认第${props.chapterNumber}章的最终版本`,
        line1: '💾 保存您的选择...',
        line2: '✍️ 生成最终摘要...'
      }
    default:
      return {
        title: '请稍候...',
        line1: '正在处理您的请求...',
        line2: '...'
      }
  }
})

watch(
  () => [props.chapterNumber, props.status, props.generationStartedAt],
  () => {
    localStartAt.value = Date.now()
  },
  { immediate: true }
)

onMounted(() => {
  timer = window.setInterval(() => {
    clockNow.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
})
</script>

<style scoped>
.m3-pulse {
  animation: m3-pulse 1.6s ease-in-out infinite;
}

@keyframes m3-pulse {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}
</style>
