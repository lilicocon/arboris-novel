<!-- AIMETA P=章节大纲区_大纲展示|R=大纲列表|NR=不含编辑功能|E=component:ChapterOutlineSection|X=ui|A=大纲组件|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="space-y-6">
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
      <div>
        <h2 class="text-2xl font-bold text-slate-900">章节大纲</h2>
        <p class="text-sm text-slate-500">故事结构与章节节奏一目了然</p>
      </div>
      <div v-if="editable" class="flex items-center gap-2">
        <button
          v-if="missingCount > 0"
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 rounded-lg disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="aiBusy"
          @click="$emit('fill-missing')"
        >
          一键补齐缺失
        </button>
        <button
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm font-medium text-emerald-700 bg-emerald-50 hover:bg-emerald-100 rounded-lg disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="aiBusy"
          @click="$emit('expand-ai')"
        >
          AI 扩写大纲
        </button>
        <button
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm font-medium text-rose-700 bg-rose-50 hover:bg-rose-100 rounded-lg disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="aiBusy || needsRegenCount === 0"
          @click="$emit('reroll-marked')"
        >
          重抽待重做章节
        </button>
        <button
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm font-medium text-fuchsia-700 bg-fuchsia-50 hover:bg-fuchsia-100 rounded-lg disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="aiBusy || selectedChapterNumbers.length === 0"
          @click="$emit('reroll-selected', selectedChapterNumbers)"
        >
          重抽已选章节
        </button>
        <button
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg"
          @click="$emit('add')"
        >
          <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 5a1 1 0 011 1v3h3a1 1 0 110 2h-3v3a1 1 0 11-2 0v-3H6a1 1 0 110-2h3V6a1 1 0 011-1z" clip-rule="evenodd" />
          </svg>
          新增章节
        </button>
        <button
          type="button"
          class="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-indigo-600 transition-colors"
          @click="emitEdit('chapter_outline', '章节大纲', outline)"
        >
          <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
            <path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" />
            <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd" />
          </svg>
          编辑大纲
        </button>
      </div>
    </div>

    <div
      v-if="missingCount > 0"
      class="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      检测到章节大纲存在缺口，共缺失 {{ missingCount }} 章。
      缺失区间：
      {{ missingRanges.map((item) => `第${item.start_chapter}-${item.end_chapter}章`).join('、') }}
    </div>

    <ol class="relative border-l border-slate-200 ml-3 space-y-8">
      <li
        v-for="chapter in outline"
        :key="chapter.chapter_number"
        class="ml-6"
      >
        <span class="absolute -left-3 mt-1 flex h-6 w-6 items-center justify-center rounded-full bg-indigo-500 text-white text-xs font-semibold">
          {{ chapter.chapter_number }}
        </span>
        <div class="bg-white/95 rounded-2xl border border-slate-200 shadow-sm p-5">
          <div v-if="editable" class="mb-3 flex justify-end">
            <label class="inline-flex items-center gap-2 text-xs text-slate-500">
              <input
                type="checkbox"
                class="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                :checked="selectedChapterNumbers.includes(chapter.chapter_number)"
                @change="toggleSelection(chapter.chapter_number)"
              />
              选中
            </label>
          </div>
          <div class="flex items-center justify-between gap-4">
            <h3 class="text-lg font-semibold text-slate-900">{{ chapter.title || `第${chapter.chapter_number}章` }}</h3>
            <div class="flex items-center gap-2">
              <span
                class="text-xs rounded-full px-2 py-1"
                :class="statusClass(chapter.status)"
              >
                {{ statusLabel(chapter.status) }}
              </span>
              <span class="text-xs text-slate-400">#{{ chapter.chapter_number }}</span>
            </div>
          </div>
          <p class="mt-3 text-sm text-slate-600 leading-6 whitespace-pre-line">{{ chapter.summary || '暂无摘要' }}</p>
          <div v-if="editable" class="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
              @click="$emit('mark-status', { chapterNumber: chapter.chapter_number, status: 'approved' })"
            >
              标记满意
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100"
              @click="$emit('mark-status', { chapterNumber: chapter.chapter_number, status: 'needs_regen' })"
            >
              标记重做
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              @click="$emit('mark-status', { chapterNumber: chapter.chapter_number, status: 'draft' })"
            >
              重置为草稿
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
              :disabled="aiBusy"
              @click="$emit('reroll-single', chapter.chapter_number)"
            >
              重抽本章
            </button>
          </div>
        </div>
      </li>
      <li v-if="!outline.length" class="ml-6 text-slate-400 text-sm">暂无章节大纲</li>
    </ol>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

interface OutlineItem {
  chapter_number: number
  title: string
  summary: string
  status?: 'draft' | 'approved' | 'needs_regen'
}

interface MissingRange {
  start_chapter: number
  end_chapter: number
  count: number
}

const props = withDefaults(defineProps<{
  outline: OutlineItem[]
  editable?: boolean
  missingRanges?: MissingRange[]
  missingCount?: number
  aiBusy?: boolean
}>(), {
  editable: false,
  missingRanges: () => [],
  missingCount: 0,
  aiBusy: false
})

const emit = defineEmits<{
  (e: 'edit', payload: { field: string; title: string; value: any }): void
  (e: 'add'): void
  (e: 'fill-missing'): void
  (e: 'expand-ai'): void
  (e: 'reroll-marked'): void
  (e: 'reroll-selected', chapterNumbers: number[]): void
  (e: 'reroll-single', chapterNumber: number): void
  (e: 'mark-status', payload: { chapterNumber: number; status: 'draft' | 'approved' | 'needs_regen' }): void
}>()

const emitEdit = (field: string, title: string, value: any) => {
  if (!props.editable) return
  emit('edit', { field, title, value })
}

const missingRanges = computed(() => props.missingRanges)
const missingCount = computed(() => props.missingCount)
const needsRegenCount = computed(() => props.outline.filter((item) => item.status === 'needs_regen').length)
const selectedChapterNumbers = ref<number[]>([])

watch(
  () => props.outline.map((item) => item.chapter_number),
  (numbers) => {
    selectedChapterNumbers.value = selectedChapterNumbers.value.filter((item) => numbers.includes(item))
  },
  { immediate: true }
)

const toggleSelection = (chapterNumber: number) => {
  if (selectedChapterNumbers.value.includes(chapterNumber)) {
    selectedChapterNumbers.value = selectedChapterNumbers.value.filter((item) => item !== chapterNumber)
    return
  }
  selectedChapterNumbers.value = [...selectedChapterNumbers.value, chapterNumber].sort((a, b) => a - b)
}

const statusLabel = (status?: OutlineItem['status']) => {
  switch (status) {
    case 'approved':
      return '已锁定'
    case 'needs_regen':
      return '待重做'
    default:
      return '草稿'
  }
}

const statusClass = (status?: OutlineItem['status']) => {
  switch (status) {
    case 'approved':
      return 'bg-emerald-50 text-emerald-700'
    case 'needs_regen':
      return 'bg-rose-50 text-rose-700'
    default:
      return 'bg-slate-100 text-slate-700'
  }
}
</script>

<script lang="ts">
import { defineComponent } from 'vue'

export default defineComponent({
  name: 'ChapterOutlineSection'
})
</script>
