<!-- AIMETA P=写作台头部_顶部导航栏|R=导航_操作按钮|NR=不含内容区域|E=component:WDHeader|X=ui|A=头部组件|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="md-top-app-bar md-elevation-1 flex-shrink-0 z-30 backdrop-blur-md">
    <div class="w-full px-4 sm:px-6 lg:px-8">
      <div class="flex items-center justify-between h-16">
        <!-- 左侧：项目信息 -->
        <div class="flex items-center gap-2 sm:gap-4 min-w-0">
          <button @click="$emit('goBack')" class="md-icon-btn md-ripple flex-shrink-0">
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L4.414 9H17a1 1 0 110 2H4.414l5.293 5.293a1 1 0 010 1.414z" clip-rule="evenodd"></path>
            </svg>
          </button>
          <div class="min-w-0">
            <h1 class="md-title-large font-semibold truncate">{{ project?.title || '加载中...' }}</h1>
            <div class="hidden sm:flex items-center gap-2 md:gap-4 md-body-small md-on-surface-variant">
              <span>{{ project?.blueprint?.genre || '--' }}</span>
              <span class="hidden md:inline">•</span>
              <span class="hidden md:inline">{{ progress }}% 完成</span>
              <span class="hidden lg:inline">•</span>
              <span class="hidden lg:inline">{{ completedChapters }}/{{ totalChapters }} 章</span>
            </div>
          </div>
        </div>

        <!-- 右侧：操作按钮 -->
        <div class="flex items-center gap-1 sm:gap-2 relative">
          <button
            @click="$emit('exportTxt')"
            :disabled="!canExportTxt"
            class="hidden md:flex md-btn md-btn-text md-ripple items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M3 14a1 1 0 011-1h3v-3a1 1 0 112 0v3h3a1 1 0 110 2H9v3a1 1 0 11-2 0v-3H4a1 1 0 01-1-1zm11-9H9a1 1 0 110-2h5a2 2 0 012 2v5a1 1 0 11-2 0V5z" clip-rule="evenodd"></path>
            </svg>
            <span class="hidden md:inline">导出 TXT</span>
          </button>
          <button @click="$emit('viewProjectDetail')" class="hidden md:flex md-btn md-btn-text md-ripple items-center gap-2">
            <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path>
              <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path>
            </svg>
            <span class="hidden md:inline">项目详情</span>
          </button>
          <div class="w-px h-6 hidden md:block" style="background-color: var(--md-outline-variant);"></div>
          <button @click="handleLogout" class="hidden md:flex md-btn md-btn-text md-ripple items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            <span class="hidden md:inline">退出登录</span>
          </button>

          <!-- 移动端菜单 (Headless UI: role="menu", ESC, click-outside) -->
          <Menu as="div" class="relative md:hidden">
            <MenuButton class="md-icon-btn md-ripple" aria-label="菜单">
              <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6 10a2 2 0 114 0 2 2 0 01-4 0zm4-6a2 2 0 100 4 2 2 0 000-4zm0 12a2 2 0 100 4 2 2 0 000-4z"></path>
              </svg>
            </MenuButton>
            <MenuItems
              class="absolute right-0 top-14 w-44 rounded-xl border bg-white shadow-lg p-2 focus:outline-none"
              style="border-color: var(--md-outline-variant);"
            >
              <MenuItem as="template" v-slot="{ active }">
                <button
                  :class="['w-full text-left px-3 py-2 rounded-lg text-sm', active ? 'bg-slate-100' : '']"
                  @click="$emit('viewProjectDetail')"
                >
                  项目详情
                </button>
              </MenuItem>
              <MenuItem as="template" :disabled="!canExportTxt" v-slot="{ active, disabled }">
                <button
                  :class="['w-full text-left px-3 py-2 rounded-lg text-sm', active && !disabled ? 'bg-slate-100' : '', disabled ? 'opacity-50 cursor-not-allowed' : '']"
                  :disabled="disabled"
                  @click="$emit('exportTxt')"
                >
                  导出 TXT
                </button>
              </MenuItem>
              <MenuItem as="template" v-slot="{ active }">
                <button
                  :class="['w-full text-left px-3 py-2 rounded-lg text-sm text-rose-600', active ? 'bg-rose-50' : '']"
                  @click="handleLogout"
                >
                  退出登录
                </button>
              </MenuItem>
            </MenuItems>
          </Menu>

          <button
            @click="$emit('toggleSidebar')"
            class="md-icon-btn md-ripple lg:hidden"
          >
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M3 5a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM3 10a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM3 15a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clip-rule="evenodd"></path>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Menu, MenuButton, MenuItems, MenuItem } from '@headlessui/vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import type { NovelProject } from '@/api/novel'

const router = useRouter()
const authStore = useAuthStore()

const handleLogout = () => {
  authStore.logout()
  router.push('/login')
}

interface Props {
  project: NovelProject | null
  progress: number
  completedChapters: number
  totalChapters: number
  canExportTxt: boolean
}

defineProps<Props>()

const emit = defineEmits(['goBack', 'viewProjectDetail', 'toggleSidebar', 'exportTxt'])
</script>
