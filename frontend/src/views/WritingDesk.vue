<!-- AIMETA P=写作台_章节编辑主页面|R=写作界面_章节管理|NR=不含详情展示|E=route:/novel/:id#component:WritingDesk|X=ui|A=写作台|D=vue|S=dom,net|RD=./README.ai -->
<template>
  <div class="m3-shell h-screen flex flex-col overflow-hidden">
    <WDHeader
      :project="project"
      :progress="progress"
      :completed-chapters="completedChapters"
      :total-chapters="totalChapters"
      :can-export-txt="canExportTxt"
      @go-back="goBack"
      @export-txt="downloadConfirmedChaptersTxt"
      @view-project-detail="viewProjectDetail"
      @toggle-sidebar="toggleSidebar"
    />

    <!-- 主要内容区域 -->
    <div class="flex-1 w-full px-4 sm:px-6 lg:px-8 py-6 overflow-hidden">
      <!-- 加载状态 -->
      <div v-if="novelStore.isLoading" class="h-full flex justify-center items-center">
        <div class="text-center">
          <div class="md-spinner mx-auto mb-4"></div>
          <p class="md-body-medium md-on-surface-variant">正在加载项目数据...</p>
        </div>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="novelStore.error" class="text-center py-20">
        <div class="md-card md-card-outlined p-8 max-w-md mx-auto" style="border-radius: var(--md-radius-xl);">
          <div class="w-12 h-12 rounded-full mx-auto mb-4 flex items-center justify-center" style="background-color: var(--md-error-container);">
            <svg class="w-6 h-6" style="color: var(--md-error);" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path>
            </svg>
          </div>
          <h3 class="md-title-large mb-2" style="color: var(--md-on-surface);">加载失败</h3>
          <p class="md-body-medium mb-4" style="color: var(--md-error);">{{ novelStore.error }}</p>
          <button @click="loadProject" class="md-btn md-btn-tonal md-ripple">重新加载</button>
        </div>
      </div>

      <!-- 主要内容 -->
      <div v-else-if="project" class="h-full flex gap-6">
        <WDSidebar
          :project="project"
          :sidebar-open="sidebarOpen"
          :selected-chapter-number="selectedChapterNumber"
          :generating-chapter="generatingChapter"
          :evaluating-chapter="evaluatingChapter"
          :is-generating-outline="isGeneratingOutline"
          :is-batch-generating="isBatchGenerating"
          :is-batch-stopping="isBatchStopping"
          :batch-current-chapter-number="batchCurrentChapterNumber"
          :batch-attempt="batchAttempt"
          :batch-max-attempts="BATCH_MAX_ATTEMPTS"
          :batch-last-error="batchLastError"
          @close-sidebar="closeSidebar"
          @select-chapter="selectChapter"
          @generate-chapter="generateChapter"
          @edit-chapter="openEditChapterModal"
          @delete-chapter="deleteChapter"
          @generate-outline="generateOutline"
          @start-batch-generate="startBatchGenerate"
          @stop-batch-generate="stopBatchGenerate"
        />

        <div class="flex-1 min-w-0">
          <WDWorkspace
            :project="project"
            :selected-chapter-number="selectedChapterNumber"
          :generating-chapter="generatingChapter"
          :evaluating-chapter="evaluatingChapter"
          :show-version-selector="showVersionSelector"
          :chapter-generation-result="chapterGenerationResult"
          :selected-version-index="selectedVersionIndex"
          :available-versions="availableVersions"
          :is-selecting-version="isSelectingVersion"
          @regenerate-chapter="regenerateChapter"
          @evaluate-chapter="evaluateChapter"
          @hide-version-selector="hideVersionSelector"
          @update:selected-version-index="selectedVersionIndex = $event"
          @show-version-detail="showVersionDetail"
          @confirm-version-selection="confirmVersionSelection"
          @generate-chapter="generateChapter"
          @show-evaluation-detail="showEvaluationDetailModal = true"
          @fetch-chapter-status="fetchChapterStatus"
          @edit-chapter="editChapterContent"
          />
        </div>
      </div>
    </div>
    <WDVersionDetailModal
      :show="showVersionDetailModal"
      :detail-version-index="detailVersionIndex"
      :version="availableVersions[detailVersionIndex] ?? null"
      :is-current="isCurrentVersion(detailVersionIndex)"
      @close="closeVersionDetail"
      @select-version="selectVersionFromDetail"
    />
    <WDEvaluationDetailModal
      :show="showEvaluationDetailModal"
      :evaluation="selectedChapter?.evaluation || null"
      @close="showEvaluationDetailModal = false"
    />
    <WDEditChapterModal
      :show="showEditChapterModal"
      :chapter="editingChapter"
      @close="showEditChapterModal = false"
      @save="saveChapterChanges"
    />
    <WDGenerateOutlineModal
      :show="showGenerateOutlineModal"
      @close="showGenerateOutlineModal = false"
      @generate="handleGenerateOutline"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useNovelStore } from '@/stores/novel'
import { NovelAPI } from '@/api/novel'
import type { AdvancedGenerateResponse, Chapter, ChapterOutline, ChapterGenerationResponse, ChapterVersion } from '@/api/novel'
import { globalAlert } from '@/composables/useAlert'
import Tooltip from '@/components/Tooltip.vue'
import WDHeader from '@/components/writing-desk/WDHeader.vue'
import WDSidebar from '@/components/writing-desk/WDSidebar.vue'
import WDWorkspace from '@/components/writing-desk/WDWorkspace.vue'
import WDVersionDetailModal from '@/components/writing-desk/WDVersionDetailModal.vue'
import WDEvaluationDetailModal from '@/components/writing-desk/WDEvaluationDetailModal.vue'
import WDEditChapterModal from '@/components/writing-desk/WDEditChapterModal.vue'
import WDGenerateOutlineModal from '@/components/writing-desk/WDGenerateOutlineModal.vue'

interface Props {
  id: string
}

const props = defineProps<Props>()
const router = useRouter()
const novelStore = useNovelStore()

// 状态管理
const selectedChapterNumber = ref<number | null>(null)
const chapterGenerationResult = ref<ChapterGenerationResponse | null>(null)
const selectedVersionIndex = ref<number>(0)
const generatingChapter = ref<number | null>(null)
const sidebarOpen = ref(false)
const showVersionDetailModal = ref(false)
const detailVersionIndex = ref<number>(0)
const showEvaluationDetailModal = ref(false)
const showEditChapterModal = ref(false)
const editingChapter = ref<ChapterOutline | null>(null)
const isGeneratingOutline = ref(false)
const showGenerateOutlineModal = ref(false)
const isFetchingChapterStatus = ref(false)
const isBatchGenerating = ref(false)
const isBatchStopping = ref(false)
const batchCurrentChapterNumber = ref<number | null>(null)
const batchAttempt = ref(0)
const batchLastError = ref<string | null>(null)
const batchStopRequested = ref(false)

const BATCH_MAX_ATTEMPTS = 5
const BATCH_RETRY_DELAY_MS = 2000

// 计算属性
const project = computed(() => novelStore.currentProject)

const selectedChapter = computed(() => {
  if (!project.value || selectedChapterNumber.value === null) return null
  return project.value.chapters.find(ch => ch.chapter_number === selectedChapterNumber.value) || null
})

const showVersionSelector = computed(() => {
  if (!selectedChapter.value) return false
  const status = selectedChapter.value.generation_status
  return status === 'waiting_for_confirm' || status === 'evaluating' || status === 'evaluation_failed' || status === 'selecting'
})

const evaluatingChapter = computed(() => {
  if (selectedChapter.value?.generation_status === 'evaluating') {
    return selectedChapter.value.chapter_number
  }
  return null
})

const isSelectingVersion = computed(() => {
  return selectedChapter.value?.generation_status === 'selecting'
})

const selectedChapterOutline = computed(() => {
  if (!project.value?.blueprint?.chapter_outline || selectedChapterNumber.value === null) return null
  return project.value.blueprint.chapter_outline.find(ch => ch.chapter_number === selectedChapterNumber.value) || null
})

const progress = computed(() => {
  if (!project.value?.blueprint?.chapter_outline) return 0
  const totalChapters = project.value.blueprint.chapter_outline.length
  const completedChapters = project.value.chapters.filter(ch => ch.content).length
  return Math.round((completedChapters / totalChapters) * 100)
})

const totalChapters = computed(() => {
  return project.value?.blueprint?.chapter_outline?.length || 0
})

const completedChapters = computed(() => {
  return project.value?.chapters?.filter(ch => ch.content)?.length || 0
})

const canExportTxt = computed(() => completedChapters.value > 0)

const isCurrentVersion = (versionIndex: number) => {
  if (!selectedChapter.value?.content || !availableVersions.value?.[versionIndex]?.content) return false

  // 使用cleanVersionContent函数清理内容进行比较
  const cleanCurrentContent = cleanVersionContent(selectedChapter.value.content)
  const cleanVersionContentStr = cleanVersionContent(availableVersions.value[versionIndex].content)

  return cleanCurrentContent === cleanVersionContentStr
}

const cleanVersionContent = (content: string): string => {
  if (!content) return ''

  // 尝试解析JSON，看是否是完整的章节对象
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
      // 如果是章节对象/数组，提取正文
      content = extracted
    }
  } catch (error) {
    // 如果不是JSON，继续处理字符串
  }

  // 去掉开头和结尾的引号
  let cleaned = content.replace(/^"|"$/g, '')

  // 处理转义字符
  cleaned = cleaned.replace(/\\n/g, '\n')  // 换行符
  cleaned = cleaned.replace(/\\"/g, '"')   // 引号
  cleaned = cleaned.replace(/\\t/g, '\t')  // 制表符
  cleaned = cleaned.replace(/\\\\/g, '\\') // 反斜杠

  return cleaned
}

const canGenerateChapter = (chapterNumber: number) => {
  if (!project.value?.blueprint?.chapter_outline) return false

  // 检查前面所有章节是否都已成功生成
  const outlines = project.value.blueprint.chapter_outline.sort((a, b) => a.chapter_number - b.chapter_number)
  
  for (const outline of outlines) {
    if (outline.chapter_number >= chapterNumber) break
    
    const chapter = project.value?.chapters.find(ch => ch.chapter_number === outline.chapter_number)
    if (!chapter || chapter.generation_status !== 'successful') {
      return false // 前面有章节未完成
    }
  }

  // 检查当前章节是否已经完成
  const currentChapter = project.value?.chapters.find(ch => ch.chapter_number === chapterNumber)
  if (currentChapter && currentChapter.generation_status === 'successful') {
    return true // 已完成的章节可以重新生成
  }

  return true // 前面章节都完成了，可以生成当前章节
}

const isChapterFailed = (chapterNumber: number) => {
  if (!project.value?.chapters) return false
  const chapter = project.value.chapters.find(ch => ch.chapter_number === chapterNumber)
  return chapter && chapter.generation_status === 'failed'
}

const hasChapterInProgress = (chapterNumber: number) => {
  if (!project.value?.chapters) return false
  const chapter = project.value.chapters.find(ch => ch.chapter_number === chapterNumber)
  // waiting_for_confirm状态表示等待选择版本 = 进行中状态
  return chapter && chapter.generation_status === 'waiting_for_confirm'
}

const extractVersionContent = (raw: unknown): string => {
  if (typeof raw !== 'string') {
    return ''
  }
  const trimmed = raw.trim()
  if (!trimmed) {
    return ''
  }

  const likelyJson = (
    (trimmed.startsWith('{') && trimmed.endsWith('}'))
    || (trimmed.startsWith('[') && trimmed.endsWith(']'))
  )
  if (!likelyJson) {
    return raw
  }

  try {
    const parsed = JSON.parse(trimmed)
    if (parsed && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>
      for (const key of ['content', 'chapter_content', 'chapter_text', 'text', 'body', 'story']) {
        const candidate = record[key]
        if (typeof candidate === 'string' && candidate.trim()) {
          return candidate
        }
      }
    }
  } catch {
    // ignore parse errors, fallback to raw text
  }
  return raw
}

// 可用版本列表（来源优先级：生成结果 > 章节 versions > 章节 content 兜底）
const availableVersions = computed<ChapterVersion[]>(() => {
  if (Array.isArray(chapterGenerationResult.value?.versions) && chapterGenerationResult.value.versions.length > 0) {
    return chapterGenerationResult.value.versions.filter((item) => Boolean(item?.content?.trim()))
  }

  const chapter = selectedChapter.value
  if (!chapter) {
    return []
  }

  if (Array.isArray(chapter.versions) && chapter.versions.length > 0) {
    const converted = chapter.versions
      .map((versionRaw) => {
        const content = extractVersionContent(versionRaw)
        if (!content.trim()) {
          return null
        }
        return {
          content,
          style: '标准'
        } as ChapterVersion
      })
      .filter((item): item is ChapterVersion => item !== null)
    if (converted.length > 0) {
      return converted
    }
  }

  if (typeof chapter.content === 'string' && chapter.content.trim()) {
    return [{ content: chapter.content, style: '标准' }]
  }

  return []
})


// 方法
const goBack = () => {
  router.push('/workspace')
}

const viewProjectDetail = () => {
  if (project.value) {
    router.push(`/detail/${project.value.id}`)
  }
}

const toggleSidebar = () => {
  sidebarOpen.value = !sidebarOpen.value
}

const closeSidebar = () => {
  sidebarOpen.value = false
}

const loadProject = async () => {
  try {
    await novelStore.loadProject(props.id)
  } catch (error) {
    console.error('加载项目失败:', error)
  }
}

const fetchChapterStatus = async () => {
  if (selectedChapterNumber.value === null || isFetchingChapterStatus.value) {
    return
  }
  const chapterNumber = selectedChapterNumber.value
  isFetchingChapterStatus.value = true
  try {
    await novelStore.loadChapter(chapterNumber)
    console.log('Chapter status polled and updated.')
  } catch (error) {
    console.error('轮询章节状态失败:', error)
    // 在这里可以决定是否要通知用户轮询失败
  } finally {
    isFetchingChapterStatus.value = false
  }
}


// 显示版本详情
const showVersionDetail = (versionIndex: number) => {
  if (versionIndex < 0 || versionIndex >= availableVersions.value.length) {
    return
  }
  detailVersionIndex.value = versionIndex
  showVersionDetailModal.value = true
}

// 关闭版本详情弹窗
const closeVersionDetail = () => {
  showVersionDetailModal.value = false
}

// 隐藏版本选择器，返回内容视图
const hideVersionSelector = () => {
  // Now controlled by computed property, but we can clear the generation result
  chapterGenerationResult.value = null
  selectedVersionIndex.value = 0
}

const selectChapter = (chapterNumber: number) => {
  selectedChapterNumber.value = chapterNumber
  chapterGenerationResult.value = null
  selectedVersionIndex.value = 0
  closeSidebar()
}

const setLocalGeneratingState = (chapterNumber: number) => {
  if (!project.value?.chapters) {
    return
  }

  const nowIso = new Date().toISOString()
  const chapter = project.value.chapters.find(ch => ch.chapter_number === chapterNumber)
  if (chapter) {
    chapter.generation_status = 'generating'
    chapter.generation_progress = 0
    chapter.generation_step = 'context_prep'
    chapter.generation_step_index = 1
    chapter.generation_step_total = null
    chapter.generation_started_at = nowIso
    chapter.status_updated_at = nowIso
    return
  }

  const outline = project.value.blueprint?.chapter_outline?.find(item => item.chapter_number === chapterNumber)
  project.value.chapters.push({
    chapter_number: chapterNumber,
    title: outline?.title || '加载中...',
    summary: outline?.summary || '',
    content: '',
    versions: [],
    evaluation: null,
    generation_status: 'generating',
    generation_progress: 0,
    generation_step: 'context_prep',
    generation_step_index: 1,
    generation_step_total: null,
    generation_started_at: nowIso,
    status_updated_at: nowIso
  } as Chapter)
}

const resolveTargetWordCount = (targetWordCount?: number) => (
  typeof targetWordCount === 'number' && targetWordCount > 0
    ? targetWordCount
    : project.value?.blueprint?.chapter_length ?? 3000
)

const getSortedOutlineNumbers = (): number[] => {
  if (!project.value?.blueprint?.chapter_outline) {
    return []
  }
  return [...project.value.blueprint.chapter_outline]
    .sort((a, b) => a.chapter_number - b.chapter_number)
    .map(item => item.chapter_number)
}

const resolveBatchStartChapter = (): number | null => {
  const chapterNumbers = getSortedOutlineNumbers()
  if (chapterNumbers.length === 0) {
    return null
  }

  if (selectedChapterNumber.value !== null) {
    const selectedNumber = selectedChapterNumber.value
    const currentStatus = project.value?.chapters.find(
      ch => ch.chapter_number === selectedNumber
    )?.generation_status
    if (currentStatus === 'successful') {
      return chapterNumbers.find(number => number > selectedNumber) ?? null
    }
    return selectedNumber
  }

  return chapterNumbers.find(number => {
    const status = project.value?.chapters.find(ch => ch.chapter_number === number)?.generation_status
    return status !== 'successful'
  }) ?? null
}

const getNextChapterNumber = (chapterNumber: number): number | null => {
  const chapterNumbers = getSortedOutlineNumbers()
  return chapterNumbers.find(number => number > chapterNumber) ?? null
}

const waitForRetry = (ms: number) => new Promise((resolve) => {
  window.setTimeout(resolve, ms)
})

const getErrorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '未知错误'
}

const getBestVariant = (result: AdvancedGenerateResponse) => {
  return result.variants.find(item => item.index === result.best_version_index) ?? null
}

const runBatchChapterAttempt = async (chapterNumber: number, targetWordCount: number) => {
  generatingChapter.value = chapterNumber
  selectedChapterNumber.value = chapterNumber
  chapterGenerationResult.value = null
  selectedVersionIndex.value = 0
  setLocalGeneratingState(chapterNumber)

  const result = await novelStore.generateChapter(chapterNumber, targetWordCount)
  const bestVariant = getBestVariant(result)
  if (!bestVariant || !bestVariant.content.trim()) {
    throw new Error('没有可用于自动定稿的版本')
  }

  await novelStore.finalizeChapter(chapterNumber, bestVariant.version_id)

  const chapter = project.value?.chapters.find(ch => ch.chapter_number === chapterNumber)
  if (!chapter || chapter.generation_status !== 'successful' || !chapter.content?.trim()) {
    throw new Error('章节定稿后状态异常')
  }
}

const startBatchGenerate = async (targetWordCount?: number) => {
  if (isBatchGenerating.value) {
    return
  }

  const startChapterNumber = resolveBatchStartChapter()
  if (startChapterNumber === null) {
    await globalAlert.showAlert('当前没有可连续生成的章节。', 'info', '无需处理')
    return
  }

  const resolvedTargetWordCount = resolveTargetWordCount(targetWordCount)
  isBatchGenerating.value = true
  isBatchStopping.value = false
  batchStopRequested.value = false
  batchCurrentChapterNumber.value = null
  batchAttempt.value = 0
  batchLastError.value = null
  closeSidebar()

  let currentChapterNumber: number | null = startChapterNumber
  try {
    while (currentChapterNumber !== null) {
      if (batchStopRequested.value) {
        break
      }

      batchCurrentChapterNumber.value = currentChapterNumber
      selectedChapterNumber.value = currentChapterNumber
      let chapterSucceeded = false

      for (let attempt = 1; attempt <= BATCH_MAX_ATTEMPTS; attempt += 1) {
        if (batchStopRequested.value) {
          break
        }

        batchAttempt.value = attempt
        batchLastError.value = null

        try {
          await runBatchChapterAttempt(currentChapterNumber, resolvedTargetWordCount)
          chapterSucceeded = true
          break
        } catch (error) {
          batchLastError.value = getErrorMessage(error)
          if (attempt < BATCH_MAX_ATTEMPTS && !batchStopRequested.value) {
            await waitForRetry(BATCH_RETRY_DELAY_MS)
          }
        } finally {
          generatingChapter.value = null
        }
      }

      if (batchStopRequested.value) {
        break
      }

      if (!chapterSucceeded) {
        await globalAlert.showError(
          `第 ${currentChapterNumber} 章已连续尝试 ${BATCH_MAX_ATTEMPTS} 次，仍然失败：${batchLastError.value || '未知错误'}`,
          '连续生成已停止'
        )
        return
      }

      currentChapterNumber = getNextChapterNumber(currentChapterNumber)
    }

    if (batchStopRequested.value) {
      await globalAlert.showAlert('连续生成已按你的要求停止。', 'info', '已停止')
      return
    }

    await globalAlert.showSuccess('已按顺序完成可生成章节。', '连续生成完成')
  } finally {
    generatingChapter.value = null
    isBatchGenerating.value = false
    isBatchStopping.value = false
    batchStopRequested.value = false
    batchCurrentChapterNumber.value = null
    batchAttempt.value = 0
  }
}

const stopBatchGenerate = () => {
  if (!isBatchGenerating.value || isBatchStopping.value) {
    return
  }
  batchStopRequested.value = true
  isBatchStopping.value = true
}

const sanitizeFileName = (name: string) => name.replace(/[\\/:*?"<>|]/g, '_')

const downloadConfirmedChaptersTxt = async () => {
  if (!project.value || !canExportTxt.value) {
    return
  }

  try {
    const blob = await NovelAPI.downloadConfirmedChaptersTxt(project.value.id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const safeTitle = sanitizeFileName(project.value.title || 'novel') || 'novel'
    link.href = url
    link.download = `${safeTitle}.txt`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  } catch (error) {
    await globalAlert.showError(`导出 TXT 失败: ${getErrorMessage(error)}`, '导出失败')
  }
}

const generateChapter = async (chapterNumber: number, targetWordCount?: number) => {
  if (isBatchGenerating.value) {
    await globalAlert.showAlert('连续生成进行中，请先停止后再手动操作。', 'info', '当前不可操作')
    return
  }

  // 检查是否可以生成该章节
  if (!canGenerateChapter(chapterNumber) && !isChapterFailed(chapterNumber) && !hasChapterInProgress(chapterNumber)) {
    globalAlert.showError('请按顺序生成章节，先完成前面的章节', '生成受限')
    return
  }

  const resolvedTargetWordCount = resolveTargetWordCount(targetWordCount)

  try {
    generatingChapter.value = chapterNumber
    selectedChapterNumber.value = chapterNumber
    chapterGenerationResult.value = null
    setLocalGeneratingState(chapterNumber)

    await novelStore.generateChapter(chapterNumber, resolvedTargetWordCount)
    // 高级生成接口不返回项目快照，这里强制拉取当前章最新状态。
    await novelStore.loadChapter(chapterNumber)

    // store 中的 project 已经被更新，所以我们不需要手动修改本地状态。
    // 单版本场景自动确认，直接进入正文视图。
    const generatedChapter = project.value?.chapters.find(ch => ch.chapter_number === chapterNumber)
    const generatedVersions = Array.isArray(generatedChapter?.versions) ? generatedChapter.versions : []
    const validVersionCount = generatedVersions
      .map((versionRaw) => extractVersionContent(versionRaw))
      .filter((content) => Boolean(content.trim()))
      .length

    if (generatedChapter?.generation_status === 'waiting_for_confirm' && validVersionCount === 1) {
      selectedVersionIndex.value = 0
      // 单版本自动确认阶段已进入“确认版本”流程，不应再被当作“生成中”渲染。
      generatingChapter.value = null
      await selectVersion(0, {
        chapterNumber,
        suppressSuccessToast: true,
        skipAvailabilityCheck: true
      })
      globalAlert.showSuccess('唯一版本已自动确认，已进入正文', '生成成功')
    } else {
      // 非单版本场景保留版本选择流程
      chapterGenerationResult.value = null
      selectedVersionIndex.value = 0
    }
  } catch (error) {
    console.error('生成章节失败:', error)

    // 错误状态的本地更新仍然是必要的，以立即反映UI
    if (project.value?.chapters) {
      const chapter = project.value.chapters.find(ch => ch.chapter_number === chapterNumber)
      if (chapter) {
        chapter.generation_status = 'failed'
        chapter.status_updated_at = new Date().toISOString()
      }
    }

    globalAlert.showError(`生成章节失败: ${error instanceof Error ? error.message : '未知错误'}`, '生成失败')
  } finally {
    generatingChapter.value = null
  }
}

const regenerateChapter = async () => {
  if (selectedChapterNumber.value !== null) {
    await generateChapter(selectedChapterNumber.value)
  }
}

const selectVersion = async (
  versionIndex: number,
  options: { chapterNumber?: number; suppressSuccessToast?: boolean; skipAvailabilityCheck?: boolean } = {}
) => {
  const targetChapterNumber = options.chapterNumber ?? selectedChapterNumber.value
  if (targetChapterNumber === null) {
    return
  }
  if (!options.skipAvailabilityCheck && !availableVersions.value?.[versionIndex]?.content) {
    return
  }

  try {
    // 在本地立即更新状态以反映UI
    if (project.value?.chapters) {
      const chapter = project.value.chapters.find(ch => ch.chapter_number === targetChapterNumber)
      if (chapter) {
        chapter.generation_status = 'selecting'
      }
    }

    selectedVersionIndex.value = versionIndex
    await novelStore.selectChapterVersion(targetChapterNumber, versionIndex)
    // 兜底同步：避免后端响应中正文字段短暂滞后导致界面需手动刷新。
    await novelStore.loadChapter(targetChapterNumber)

    // 状态更新将由 store 自动触发，本地无需手动更新
    // 轮询机制会处理状态变更，成功后会自动隐藏选择器
    // showVersionSelector.value = false
    chapterGenerationResult.value = null
    if (!options.suppressSuccessToast) {
      globalAlert.showSuccess('版本已确认', '操作成功')
    }
  } catch (error) {
    console.error('选择章节版本失败:', error)
    // 错误状态下恢复章节状态
    if (project.value?.chapters) {
      const chapter = project.value.chapters.find(ch => ch.chapter_number === targetChapterNumber)
      if (chapter) {
        chapter.generation_status = 'waiting_for_confirm' // Or the previous state
      }
    }
    globalAlert.showError(`选择章节版本失败: ${error instanceof Error ? error.message : '未知错误'}`, '选择失败')
  }
}

// 从详情弹窗中选择版本
const selectVersionFromDetail = async () => {
  selectedVersionIndex.value = detailVersionIndex.value
  await selectVersion(detailVersionIndex.value)
  closeVersionDetail()
}

const confirmVersionSelection = async () => {
  await selectVersion(selectedVersionIndex.value)
}

const openEditChapterModal = (chapter: ChapterOutline) => {
  editingChapter.value = chapter
  showEditChapterModal.value = true
}

const saveChapterChanges = async (updatedChapter: ChapterOutline) => {
  try {
    await novelStore.updateChapterOutline(updatedChapter)
    globalAlert.showSuccess('章节大纲已更新', '保存成功')
  } catch (error) {
    console.error('更新章节大纲失败:', error)
    globalAlert.showError(`更新章节大纲失败: ${error instanceof Error ? error.message : '未知错误'}`, '保存失败')
  } finally {
    showEditChapterModal.value = false
  }
}

const evaluateChapter = async () => {
  if (selectedChapterNumber.value !== null) {
    // 保存原始状态，用于失败时恢复
    let previousStatus: "not_generated" | "generating" | "evaluating" | "selecting" | "failed" | "evaluation_failed" | "waiting_for_confirm" | "successful" | undefined
    
    try {
      // 在本地更新章节状态为evaluating以立即反映在UI上
      if (project.value?.chapters) {
        const chapter = project.value.chapters.find(ch => ch.chapter_number === selectedChapterNumber.value)
        if (chapter) {
          previousStatus = chapter.generation_status // 保存原状态
          chapter.generation_status = 'evaluating'
        }
      }
      await novelStore.evaluateChapter(selectedChapterNumber.value)
      
      // 评审完成后，状态会通过store和轮询更新，这里不需要额外操作
      globalAlert.showSuccess('章节评审结果已生成', '评审成功')
    } catch (error) {
      console.error('评审章节失败:', error)
      
      // 错误状态下恢复章节状态为原始状态
      if (project.value?.chapters) {
        const chapter = project.value.chapters.find(ch => ch.chapter_number === selectedChapterNumber.value)
        if (chapter && previousStatus) {
          chapter.generation_status = previousStatus // 恢复为原状态
        }
      }
      
      globalAlert.showError(`评审章节失败: ${error instanceof Error ? error.message : '未知错误'}`, '评审失败')
    }
  }
}

const deleteChapter = async (chapterNumbers: number | number[]) => {
  const numbersToDelete = Array.isArray(chapterNumbers) ? chapterNumbers : [chapterNumbers]
  const confirmationMessage = numbersToDelete.length > 1
    ? `您确定要删除选中的 ${numbersToDelete.length} 个章节吗？这个操作无法撤销。`
    : `您确定要删除第 ${numbersToDelete[0]} 章吗？这个操作无法撤销。`

  if (window.confirm(confirmationMessage)) {
    try {
      await novelStore.deleteChapter(numbersToDelete)
      globalAlert.showSuccess('章节已删除', '操作成功')
      // If the currently selected chapter was deleted, unselect it
      if (selectedChapterNumber.value && numbersToDelete.includes(selectedChapterNumber.value)) {
        selectedChapterNumber.value = null
      }
    } catch (error) {
      console.error('删除章节失败:', error)
      globalAlert.showError(`删除章节失败: ${error instanceof Error ? error.message : '未知错误'}`, '删除失败')
    }
  }
}

const generateOutline = async () => {
  showGenerateOutlineModal.value = true
}

const editChapterContent = async (data: { chapterNumber: number, content: string }) => {
  if (!project.value) return

  try {
    await novelStore.editChapterContent(project.value.id, data.chapterNumber, data.content)
    globalAlert.showSuccess('章节内容已更新', '保存成功')
  } catch (error) {
    console.error('编辑章节内容失败:', error)
    globalAlert.showError(`编辑章节内容失败: ${error instanceof Error ? error.message : '未知错误'}`, '保存失败')
  }
}

const handleGenerateOutline = async (numChapters: number) => {
  if (!project.value) return
  isGeneratingOutline.value = true
  try {
    const startChapter = (project.value.blueprint?.chapter_outline?.length || 0) + 1
    await novelStore.generateChapterOutline(startChapter, numChapters)
    globalAlert.showSuccess('新的章节大纲已生成', '操作成功')
  } catch (error) {
    console.error('生成大纲失败:', error)
    globalAlert.showError(`生成大纲失败: ${error instanceof Error ? error.message : '未知错误'}`, '生成失败')
  } finally {
    isGeneratingOutline.value = false
  }
}

onMounted(() => {
  document.body.classList.add('m3-novel')
  loadProject()
})

onUnmounted(() => {
  document.body.classList.remove('m3-novel')
})
</script>

<style scoped>
:global(body.m3-novel) {
  --md-font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif;
  --md-primary: #2563eb;
  --md-primary-light: #4f7bf2;
  --md-primary-dark: #1d4ed8;
  --md-on-primary: #ffffff;
  --md-primary-container: #dbeafe;
  --md-on-primary-container: #0f172a;
  --md-secondary: #0f766e;
  --md-secondary-light: #2dd4bf;
  --md-secondary-dark: #0f766e;
  --md-on-secondary: #ffffff;
  --md-secondary-container: #ccfbf1;
  --md-on-secondary-container: #0f172a;
  --md-surface: #ffffff;
  --md-surface-dim: #f1f5f9;
  --md-surface-container-lowest: #ffffff;
  --md-surface-container-low: #f8fafc;
  --md-surface-container: #f1f5f9;
  --md-surface-container-high: #e2e8f0;
  --md-surface-container-highest: #dbe3ef;
  --md-on-surface: #0f172a;
  --md-on-surface-variant: #475569;
  --md-outline: #d7dde5;
  --md-outline-variant: #e2e8f0;
  --md-error: #dc2626;
  --md-error-container: #fee2e2;
  --md-on-error: #ffffff;
  --md-on-error-container: #7f1d1d;
  color: var(--md-on-surface);
  font-family: var(--md-font-family);
}

.m3-shell {
  background: radial-gradient(1200px 600px at 15% -20%, rgba(37, 99, 235, 0.16), transparent 60%),
    radial-gradient(900px 420px at 85% 0%, rgba(45, 212, 191, 0.12), transparent 55%),
    linear-gradient(140deg, #f8fafc 0%, #eef2ff 45%, #ecfeff 100%);
  color: var(--md-on-surface);
  font-family: var(--md-font-family);
  animation: m3-fade 0.6s ease-out both;
}

@media (prefers-reduced-motion: reduce) {
  .m3-shell {
    animation: none;
  }
}

/* 自定义样式 */
.line-clamp-1 {
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 自定义滚动条 */
::-webkit-scrollbar {
  width: 6px;
}

::-webkit-scrollbar-track {
  background: var(--md-surface-container);
  border-radius: 3px;
}

::-webkit-scrollbar-thumb {
  background: var(--md-outline);
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--md-on-surface-variant);
}

/* 动画效果 */
@keyframes m3-fade {
  from {
    opacity: 0;
    transform: translateY(18px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
