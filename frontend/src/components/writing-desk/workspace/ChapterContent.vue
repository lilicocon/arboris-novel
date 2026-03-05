<!-- AIMETA P=章节内容_章节文本展示编辑|R=内容展示_编辑|NR=不含版本管理|E=component:ChapterContent|X=internal|A=内容组件|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="space-y-6">
    <div class="md-card md-card-filled p-4 mb-6" style="border-radius: var(--md-radius-lg); background-color: var(--md-success-container);">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2" style="color: var(--md-on-success-container);">
          <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"></path>
          </svg>
          <span class="font-medium">这个章节已经完成</span>
        </div>

        <button
          v-if="selectedChapter.versions && selectedChapter.versions.length > 0"
          @click="$emit('showVersionSelector', true)"
          class="md-btn md-btn-text md-ripple flex items-center gap-1"
        >
          <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
            <path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path>
            <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path>
          </svg>
          查看所有版本
        </button>
      </div>
    </div>

    <div class="md-card md-card-outlined p-6" style="border-radius: var(--md-radius-xl);">
      <div class="flex items-center justify-between mb-4 gap-3">
        <h4 class="md-title-medium font-semibold">章节内容</h4>
        <div class="flex items-center gap-3">
          <div class="md-body-small md-on-surface-variant">
            约 {{ Math.round(cleanVersionContent(selectedChapter.content || '').length / 100) * 100 }} 字
          </div>
          <!-- 分层优化按钮 -->
          <button
            class="md-btn md-btn-tonal md-ripple flex items-center gap-1"
            @click="openOptimizerPanel"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
            </svg>
            分层优化
          </button>
          <button
            class="md-btn md-btn-outlined md-ripple flex items-center gap-1"
            :class="selectedChapter.content ? '' : 'opacity-50 cursor-not-allowed'"
            :disabled="!selectedChapter.content"
            @click="exportChapterAsTxt(selectedChapter)"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v16h16V4m-4 4l-4-4-4 4m4-4v12" />
            </svg>
            导出TXT
          </button>
        </div>
      </div>
      <div class="prose max-w-none">
        <div class="chapter-prose" style="color: var(--md-on-surface);">
          <p v-for="(paragraph, idx) in chapterDisplayParagraphs" :key="`chapter-${idx}`">{{ paragraph }}</p>
        </div>
      </div>
    </div>

    <!-- 分层优化弹窗 -->
    <Teleport to="body">
      <div
        v-if="showOptimizer"
        class="md-dialog-overlay"
        @click.self="closeOptimizerModal"
      >
        <div class="md-dialog m3-optimizer-dialog">
          <div class="p-6">
            <!-- 优化面板头部 -->
            <div class="flex items-center justify-between mb-6">
              <div>
                <h3 class="md-headline-small font-semibold">✨ 分层优化</h3>
                <p class="md-body-small md-on-surface-variant mt-1">选择一个维度进行深度优化，让文字更有灵魂</p>
              </div>
              <button
                @click="closeOptimizerModal"
                :disabled="isOptimizing"
                class="md-icon-btn md-ripple"
                :class="{ 'opacity-40 cursor-not-allowed': isOptimizing }"
              >
                <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                  <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                </svg>
              </button>
            </div>

            <!-- 优化维度选择 -->
            <div class="grid grid-cols-2 gap-4 mb-6">
              <button
                v-for="dim in optimizeDimensions"
                :key="dim.key"
                @click="selectedDimension = dim.key"
                :disabled="isOptimizing"
                :class="[
                  'md-card md-card-outlined p-4 text-left transition-all duration-200',
                  selectedDimension === dim.key
                    ? 'm3-option-selected'
                    : 'm3-option',
                  isOptimizing ? 'opacity-70 cursor-not-allowed' : ''
                ]"
              >
                <div class="flex items-center gap-3 mb-2">
                  <span class="text-2xl">{{ dim.icon }}</span>
                  <span class="md-title-small font-semibold">{{ dim.label }}</span>
                </div>
                <p class="md-body-small md-on-surface-variant">{{ dim.description }}</p>
              </button>
            </div>

            <!-- 额外说明 -->
            <div class="mb-6">
              <label class="md-text-field-label mb-2">
                额外优化指令（可选）
              </label>
              <textarea
                v-model="additionalNotes"
                rows="3"
                class="md-textarea w-full resize-none"
                placeholder="例如：加强主角内心的挣扎感，让对话更有张力..."
                :disabled="isOptimizing"
              ></textarea>
            </div>

            <!-- 优化进度 -->
            <div v-if="isOptimizing" class="m3-optimizing-panel mb-6">
              <div class="flex items-center gap-2 mb-2">
                <span class="md-body-small font-medium">
                  正在优化{{ selectedDimensionLabel ? `：${selectedDimensionLabel}` : '' }}
                </span>
                <span class="m3-optimizing-dots" aria-hidden="true">
                  <i></i><i></i><i></i>
                </span>
              </div>
              <p class="md-body-small md-on-surface-variant mb-3">{{ currentOptimizeHint }}</p>
              <div class="m3-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-label="优化进行中">
                <div class="m3-progress-bar"></div>
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="flex justify-end gap-3">
              <button
                @click="closeOptimizerModal"
                :disabled="isOptimizing"
                class="md-btn md-btn-outlined md-ripple disabled:opacity-50"
              >
                取消
              </button>
              <button
                @click="startOptimize"
                :disabled="!selectedDimension || isOptimizing"
                class="md-btn md-btn-filled md-ripple disabled:opacity-50 flex items-center gap-2"
              >
                <svg v-if="isOptimizing" class="w-4 h-4 animate-spin" fill="currentColor" viewBox="0 0 20 20">
                  <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
                </svg>
                {{ isOptimizing ? '优化中...' : '开始优化' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 优化结果预览弹窗 -->
    <Teleport to="body">
      <div
        v-if="showOptimizeResult"
        class="md-dialog-overlay"
        @click.self="closeOptimizeResult"
      >
        <div class="md-dialog m3-result-dialog flex flex-col">
          <div class="p-6 border-b" style="border-bottom-color: var(--md-outline-variant);">
            <div class="flex items-center justify-between">
              <div>
                <h3 class="md-headline-small font-semibold">优化结果预览</h3>
                <p class="md-body-small md-on-surface-variant mt-1">{{ optimizeResultNotes }}</p>
              </div>
              <button
                @click="closeOptimizeResult"
                class="md-icon-btn md-ripple"
              >
                <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                  <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                </svg>
              </button>
            </div>
          </div>
          <div class="flex-1 overflow-y-auto p-6">
            <div class="prose max-w-none">
              <div class="chapter-prose" style="color: var(--md-on-surface);">
                <p v-for="(paragraph, idx) in optimizedDisplayParagraphs" :key="`optimized-${idx}`">{{ paragraph }}</p>
              </div>
            </div>
          </div>
          <div class="p-6 border-t flex items-center justify-end gap-3" style="border-top-color: var(--md-outline-variant);">
            <div class="md-body-small md-on-surface-variant m3-preview-metric">
              共 {{ optimizedPreviewCharCount }} 字
            </div>
            <button
              @click="reselectOptimization"
              :disabled="isApplying"
              class="md-btn md-btn-tonal md-ripple disabled:opacity-50"
            >
              重新选择优化
            </button>
            <button
              @click="closeOptimizeResult"
              class="md-btn md-btn-outlined md-ripple"
            >
              取消
            </button>
            <button
              @click="applyOptimization"
              :disabled="isApplying"
              class="md-btn md-btn-filled md-ripple disabled:opacity-50 flex items-center gap-2"
              style="background-color: var(--md-success); color: var(--md-on-success);"
            >
              <svg v-if="isApplying" class="w-4 h-4 animate-spin" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
              </svg>
              {{ isApplying ? '应用中...' : '应用优化' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { globalAlert } from '@/composables/useAlert'
import type { Chapter } from '@/api/novel'
import { OptimizerAPI } from '@/api/novel'
import { useNovelStore } from '@/stores/novel'

interface Props {
  selectedChapter: Chapter
  projectId?: string
}

const props = defineProps<Props>()
const novelStore = useNovelStore()

defineEmits(['showVersionSelector'])

// 优化相关状态
const showOptimizer = ref(false)
const showOptimizeResult = ref(false)
const selectedDimension = ref<string>('')
const additionalNotes = ref('')
const isOptimizing = ref(false)
const isApplying = ref(false)
const optimizedContent = ref('')
const optimizeResultNotes = ref('')
const optimizeHintIndex = ref(0)
let optimizeHintTimer: number | null = null

// 优化维度配置
const optimizeDimensions = [
  {
    key: 'dialogue',
    icon: '💬',
    label: '对话优化',
    description: '让每句对话都有独特的声音和潜台词'
  },
  {
    key: 'environment',
    icon: '🌄',
    label: '环境描写',
    description: '让场景氛围与情绪完美融合'
  },
  {
    key: 'psychology',
    icon: '🧠',
    label: '心理活动',
    description: '深入角色内心，展现复杂情感'
  },
  {
    key: 'rhythm',
    icon: '🎵',
    label: '节奏韵律',
    description: '优化文字节奏，增强阅读体验'
  }
]

const optimizeHints = [
  '正在重构句式与语气，保持人物声音一致性',
  '正在增强细节密度，补充情绪与感官锚点',
  '正在检查段落节奏，确保阅读流畅且有张力',
  '正在收敛表达，避免空泛描述并强化画面感'
]

const selectedDimensionLabel = computed(() => {
  const item = optimizeDimensions.find((dim) => dim.key === selectedDimension.value)
  return item?.label ?? ''
})

const currentOptimizeHint = computed(
  () => optimizeHints[optimizeHintIndex.value % optimizeHints.length]
)

const startOptimizeHintRotation = () => {
  optimizeHintIndex.value = 0
  if (optimizeHintTimer !== null) {
    window.clearInterval(optimizeHintTimer)
  }
  optimizeHintTimer = window.setInterval(() => {
    optimizeHintIndex.value = (optimizeHintIndex.value + 1) % optimizeHints.length
  }, 1600)
}

const stopOptimizeHintRotation = () => {
  if (optimizeHintTimer !== null) {
    window.clearInterval(optimizeHintTimer)
    optimizeHintTimer = null
  }
}

const cleanVersionContent = (content: string): string => {
  if (!content) return ''
  try {
    const parsed = JSON.parse(content)
    const extractContent = (value: any): string | null => {
      if (!value) return null
      if (typeof value === 'string') return value
      if (Array.isArray(value)) {
        for (const item of value) {
          const nested = extractContent(item)
          if (nested) return nested
        }
        return null
      }
      if (typeof value === 'object') {
        for (const key of ['content', 'chapter_content', 'chapter_text', 'text', 'body', 'story']) {
          if (value[key]) {
            const nested = extractContent(value[key])
            if (nested) return nested
          }
        }
      }
      return null
    }
    const extracted = extractContent(parsed)
    if (extracted) {
      content = extracted
    }
  } catch (error) {
    // not a json
  }
  let cleaned = content.replace(/^"|"$/g, '')
  cleaned = cleaned.replace(/\\n/g, '\n')
  cleaned = cleaned.replace(/\\"/g, '"')
  cleaned = cleaned.replace(/\\t/g, '\t')
  cleaned = cleaned.replace(/\\\\/g, '\\')
  return cleaned
}

const splitChapterParagraphs = (content: string): string[] => {
  if (!content) return []
  const normalized = content
    .replace(/\r\n?/g, '\n')
    .replace(/\u00A0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  if (!normalized) return []

  const paragraphs = normalized
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)

  if (paragraphs.length !== 1) {
    return paragraphs
  }

  // 单段超长文本兜底：按句号等标点粗分段，提升可读性
  const singleParagraph = paragraphs[0]
  const sentences = (
    singleParagraph.match(/[^。！？!?；;]+[。！？!?；;]?/g)
    || [singleParagraph]
  )
    .map((sentence) => sentence.trim())
    .filter(Boolean)

  if (sentences.length < 6) {
    return paragraphs
  }

  const grouped: string[] = []
  for (let i = 0; i < sentences.length; i += 2) {
    grouped.push(`${sentences[i]}${sentences[i + 1] || ''}`.trim())
  }
  return grouped.filter(Boolean)
}

const chapterDisplayParagraphs = computed(() =>
  splitChapterParagraphs(cleanVersionContent(props.selectedChapter.content || ''))
)

const optimizedPreviewText = computed(() =>
  cleanVersionContent(optimizedContent.value || '')
)

const optimizedPreviewCharCount = computed(() => optimizedPreviewText.value.length)
const hasOptimizedResult = computed(() => Boolean(optimizedPreviewText.value.trim()))

const optimizedDisplayParagraphs = computed(() =>
  splitChapterParagraphs(optimizedPreviewText.value)
)

const openOptimizerPanel = () => {
  if (hasOptimizedResult.value) {
    showOptimizeResult.value = true
    showOptimizer.value = false
    return
  }
  showOptimizer.value = true
}

const closeOptimizeResult = () => {
  if (isApplying.value) return
  showOptimizeResult.value = false
}

const reselectOptimization = () => {
  if (isApplying.value) return
  showOptimizeResult.value = false
  showOptimizer.value = true
}

const sanitizeFileName = (name: string): string => {
  return name.replace(/[\\/:*?"<>|]/g, '_')
}

const exportChapterAsTxt = (chapter?: Chapter | null) => {
  if (!chapter) return

  const title = chapter.title?.trim() || `第${chapter.chapter_number}章`
  const safeTitle = sanitizeFileName(title) || `chapter-${chapter.chapter_number}`
  const content = cleanVersionContent(chapter.content || '')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeTitle}.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

const tryParseOptimizerPayload = (rawText: string): Record<string, unknown> | null => {
  if (!rawText) return null
  const text = rawText.trim()
  if (!text) return null

  const candidates: string[] = [text]
  const fenceMatch = text.match(/```(?:json|JSON)?\s*([\s\S]*?)\s*```/)
  if (fenceMatch?.[1]) {
    const fenced = fenceMatch[1].trim()
    if (fenced && fenced !== text) candidates.unshift(fenced)
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      // ignore
    }
  }
  return null
}

const decodeJsonStringFragment = (fragment: string): string => {
  try {
    return JSON.parse(`"${fragment}"`) as string
  } catch {
    return fragment
      .replace(/\\"/g, '"')
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '\t')
  }
}

const extractJsonField = (rawText: string, field: 'optimized_content' | 'optimization_notes'): string | null => {
  const pattern = new RegExp(`"${field}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`, 's')
  const match = rawText.match(pattern)
  if (!match?.[1]) return null
  return decodeJsonStringFragment(match[1])
}

const normalizeOptimizeResult = (
  contentRaw: string,
  notesRaw: string
): { content: string; notes: string } => {
  let content = (contentRaw || '').trim()
  let notes = (notesRaw || '').trim()
  const seen = new Set<string>()

  // 如果 optimized_content 里又套了一层 JSON，递归解开（最多 2 层，防止死循环）
  for (let i = 0; i < 2; i++) {
    if (!content || seen.has(content)) break
    seen.add(content)
    const payload = tryParseOptimizerPayload(content)
    if (!payload) break
    const nestedContent = payload.optimized_content
    if (typeof nestedContent !== 'string' || !nestedContent.trim()) break
    content = nestedContent.trim()
    if (!notes && typeof payload.optimization_notes === 'string') {
      notes = payload.optimization_notes.trim()
    }
  }

  // 非标准响应兜底：从文本中按字段提取
  if (content.includes('"optimized_content"')) {
    const extractedContent = extractJsonField(content, 'optimized_content')
    if (extractedContent?.trim()) {
      content = extractedContent.trim()
    }
    if (!notes) {
      const extractedNotes = extractJsonField(contentRaw, 'optimization_notes')
      if (extractedNotes?.trim()) {
        notes = extractedNotes.trim()
      }
    }
  }

  const fenced = content.match(/```(?:json|JSON)?\s*([\s\S]*?)\s*```/)
  if (fenced?.[1]) {
    content = fenced[1].trim()
  }

  return {
    content,
    notes: notes || '优化完成'
  }
}

const startOptimize = async () => {
  if (!selectedDimension.value) {
    globalAlert.showError('请选择优化维度')
    return
  }
  if (!props.projectId) {
    globalAlert.showError('缺少项目信息，无法执行优化')
    return
  }

  isOptimizing.value = true
  startOptimizeHintRotation()

  try {
    const result = await OptimizerAPI.optimizeChapter({
      project_id: props.projectId,
      chapter_number: props.selectedChapter.chapter_number,
      dimension: selectedDimension.value as 'dialogue' | 'environment' | 'psychology' | 'rhythm',
      additional_notes: additionalNotes.value || undefined
    })

    const normalized = normalizeOptimizeResult(result.optimized_content, result.optimization_notes)
    optimizedContent.value = normalized.content
    optimizeResultNotes.value = normalized.notes
    showOptimizer.value = false
    showOptimizeResult.value = true
  } catch (error: any) {
    console.error('优化失败:', error)
    globalAlert.showError(error.message || '优化失败，请稍后重试')
  } finally {
    stopOptimizeHintRotation()
    isOptimizing.value = false
  }
}

const closeOptimizerModal = () => {
  if (isOptimizing.value) return
  showOptimizer.value = false
}

const applyOptimization = async () => {
  if (!optimizedContent.value || !props.projectId) return

  isApplying.value = true

  try {
    await OptimizerAPI.applyOptimization(
      props.projectId,
      props.selectedChapter.chapter_number,
      optimizedContent.value
    )

    globalAlert.showSuccess('优化内容已应用')
    showOptimizeResult.value = false
    
    // 重置状态
    selectedDimension.value = ''
    additionalNotes.value = ''
    optimizedContent.value = ''
    optimizeResultNotes.value = ''

    // 仅刷新当前章节数据，避免整页刷新导致路由重载和状态丢失。
    await novelStore.loadChapter(props.selectedChapter.chapter_number)
  } catch (error: any) {
    console.error('应用优化失败:', error)
    globalAlert.showError(error.message || '应用优化失败，请稍后重试')
  } finally {
    isApplying.value = false
  }
}

onUnmounted(() => {
  stopOptimizeHintRotation()
})
</script>

<style scoped>
.m3-optimizer-dialog {
  max-width: min(720px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  border-radius: var(--md-radius-xl);
  animation: optimizer-pop-in 0.24s ease-out both;
}

.m3-result-dialog {
  max-width: min(900px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  border-radius: var(--md-radius-xl);
}

.m3-optimizing-panel {
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--md-radius-md);
  background: linear-gradient(
    120deg,
    color-mix(in srgb, var(--md-primary-container) 70%, white) 0%,
    color-mix(in srgb, var(--md-surface-container-low) 85%, white) 100%
  );
  padding: 12px 14px;
}

.m3-progress-track {
  position: relative;
  width: 100%;
  height: 6px;
  border-radius: 999px;
  overflow: hidden;
  background-color: color-mix(in srgb, var(--md-primary) 16%, white);
}

.m3-progress-bar {
  width: 45%;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--md-primary) 72%, white) 0%,
    var(--md-primary) 55%,
    color-mix(in srgb, var(--md-primary) 82%, white) 100%
  );
  animation: optimizer-progress-slide 1.05s ease-in-out infinite;
}

.m3-optimizing-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.m3-optimizing-dots i {
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: var(--md-primary);
  display: inline-block;
  animation: optimizer-dot-bounce 0.9s ease-in-out infinite;
}

.m3-optimizing-dots i:nth-child(2) {
  animation-delay: 0.12s;
}

.m3-optimizing-dots i:nth-child(3) {
  animation-delay: 0.24s;
}

.m3-option {
  border-color: var(--md-outline-variant);
}

.m3-option-selected {
  border-color: var(--md-primary);
  background-color: var(--md-primary-container);
  box-shadow: var(--md-elevation-1);
}

.chapter-prose p {
  margin: 0 0 0.9em;
  line-height: 1.9;
  text-indent: 2em;
  white-space: pre-wrap;
}

.chapter-prose p:last-child {
  margin-bottom: 0;
}

@keyframes optimizer-pop-in {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes optimizer-progress-slide {
  0% {
    transform: translateX(-120%);
  }
  100% {
    transform: translateX(240%);
  }
}

@keyframes optimizer-dot-bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  40% {
    transform: translateY(-2px);
    opacity: 1;
  }
}
</style>
