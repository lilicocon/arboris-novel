<!-- AIMETA P=设置管理_系统设置界面|R=系统配置表单|NR=不含用户设置|E=component:SettingsManagement|X=ui|A=设置组件|D=vue|S=dom,net|RD=./README.ai -->
<template>
  <n-space vertical size="large" class="admin-settings">
    <div class="top-settings-grid">
      <n-card :bordered="false" class="top-settings-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">章节生成版本数</span>
          </div>
        </template>
        <n-spin :show="configLoading || chapterVersionSaving">
          <n-alert v-if="chapterVersionError" type="error" closable @close="chapterVersionError = null">
            {{ chapterVersionError }}
          </n-alert>
          <n-form label-placement="top" class="version-form">
            <n-form-item label="每章生成候选版本数量（仅支持 1 或 2）">
              <n-input-number
                v-model:value="chapterVersionCount"
                :min="1"
                :max="2"
                :step="1"
                :precision="0"
                placeholder="请输入 1 或 2"
              />
            </n-form-item>
            <div class="form-hint">
              优先级：系统配置 <code>writer.chapter_versions</code> &gt; 环境变量 <code>WRITER_CHAPTER_VERSION_COUNT</code>
            </div>
            <n-space justify="end">
              <n-button type="primary" :loading="chapterVersionSaving" @click="saveChapterVersionCount">
                保存设置
              </n-button>
            </n-space>
          </n-form>
        </n-spin>
      </n-card>

      <n-card :bordered="false" class="top-settings-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">版本检查源</span>
          </div>
        </template>
        <n-spin :show="configLoading || versionSourceSaving">
          <n-alert v-if="versionSourceError" type="error" closable @close="versionSourceError = null">
            {{ versionSourceError }}
          </n-alert>
          <n-form label-placement="top" class="version-form">
            <n-form-item label="版本信息 JSON 地址（必填）">
              <n-input
                v-model:value="versionInfoUrl"
                placeholder="https://raw.githubusercontent.com/.../version-info.json"
              />
            </n-form-item>
            <div class="form-hint">
              优先级：系统配置 <code>updates.version_info_url</code> &gt; 环境变量 <code>VERSION_INFO_URL</code>
            </div>
            <div class="version-compare-panel">
              <div class="compare-row">
                <span>本地版本：</span>
                <code>{{ localVersion }}</code>
              </div>
              <div class="compare-row">
                <span>远程版本：</span>
                <code v-if="remoteVersion">{{ remoteVersion }}</code>
                <span v-else class="compare-empty">
                  {{ versionCheckLoading ? '正在解析 JSON...' : '未解析到版本号' }}
                </span>
              </div>
              <div v-if="remoteVersionSource" class="compare-meta">
                来源：<code>{{ remoteVersionSource }}</code>
              </div>
              <div v-if="remoteBuildTimeBeijing" class="compare-meta">
                构建时间：{{ remoteBuildTimeBeijing }}
              </div>
              <div v-if="versionCheckError" class="compare-result compare-error">
                {{ versionCheckError }}
              </div>
              <div
                v-else-if="remoteVersion"
                :class="['compare-result', hasNewVersion ? 'compare-new' : 'compare-same']"
              >
                {{ hasNewVersion ? '检测到版本差异，可能有新版本可用' : '远程与本地版本一致' }}
              </div>
            </div>
            <n-space justify="end">
              <n-button :loading="versionCheckLoading" @click="handleCheckVersionSource">
                检查版本
              </n-button>
              <n-button type="primary" :loading="versionSourceSaving" @click="saveVersionSources">
                保存地址
              </n-button>
            </n-space>
          </n-form>
        </n-spin>
      </n-card>
    </div>

    <n-card :bordered="false">
      <template #header>
        <div class="card-header">
          <span class="card-title">系统配置</span>
          <n-button type="primary" size="small" @click="openCreateModal">
            新增配置
          </n-button>
        </div>
      </template>

      <n-spin :show="configLoading">
        <n-alert v-if="configError" type="error" closable @close="configError = null">
          {{ configError }}
        </n-alert>

        <n-data-table
          :columns="columns"
          :data="configs"
          :loading="configLoading"
          :bordered="false"
          :row-key="rowKey"
          class="config-table"
        />
      </n-spin>
    </n-card>
  </n-space>

  <n-modal
    v-model:show="configModalVisible"
    preset="card"
    :title="modalTitle"
    class="config-modal"
    :style="{ width: '520px', maxWidth: '92vw' }"
  >
    <n-form label-placement="top" :model="configForm">
      <n-form-item label="Key">
        <n-input
          v-model:value="configForm.key"
          :disabled="!isCreateMode"
          placeholder="请输入唯一 Key"
        />
      </n-form-item>
      <n-form-item label="值">
        <n-input v-model:value="configForm.value" placeholder="配置的具体值" />
      </n-form-item>
      <n-form-item label="描述">
        <n-input v-model:value="configForm.description" placeholder="配置项的用途说明，可选" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button quaternary @click="closeConfigModal">取消</n-button>
        <n-button type="primary" :loading="configSaving" @click="submitConfig">
          保存
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NSpace,
  NSpin,
  type DataTableColumns
} from 'naive-ui'

import {
  API_BASE_URL,
  AdminAPI,
  type SystemConfig,
  type SystemConfigUpdatePayload,
  type SystemConfigUpsertPayload
} from '@/api/admin'
import { normalizeComparableVersion } from '@/api/version'
import { useAlert } from '@/composables/useAlert'

const { showAlert } = useAlert()

const WRITER_VERSION_CONFIG_KEY = 'writer.chapter_versions'
const LEGACY_WRITER_VERSION_CONFIG_KEY = 'writer.version_count'
const VERSION_INFO_URL_CONFIG_KEY = 'updates.version_info_url'
const LEGACY_VERSION_INFO_URL_CONFIG_KEY = 'updates.github_json_url'
const MIN_CHAPTER_VERSION_COUNT = 1
const MAX_CHAPTER_VERSION_COUNT = 2

const chapterVersionCount = ref<number>(MIN_CHAPTER_VERSION_COUNT)
const chapterVersionSaving = ref(false)
const chapterVersionError = ref<string | null>(null)
const versionInfoUrl = ref('')
const versionSourceSaving = ref(false)
const versionSourceError = ref<string | null>(null)
const localVersion = ((import.meta.env.VITE_APP_VERSION as string | undefined)?.trim()) || 'dev'
const remoteVersion = ref<string | null>(null)
const remoteVersionSource = ref<string | null>(null)
const remoteBuildTimeBeijing = ref<string | null>(null)
const versionCheckLoading = ref(false)
const versionCheckError = ref<string | null>(null)

const configs = ref<SystemConfig[]>([])
const configLoading = ref(false)
const configSaving = ref(false)
const configError = ref<string | null>(null)

const configModalVisible = ref(false)
const isCreateMode = ref(true)
const configForm = reactive<SystemConfig>({
  key: '',
  value: '',
  description: ''
})

const rowKey = (row: SystemConfig) => row.key

const modalTitle = computed(() => (isCreateMode.value ? '新增配置项' : '编辑配置项'))
const hasNewVersion = computed(() => {
  if (!remoteVersion.value) {
    return false
  }
  return normalizeComparableVersion(remoteVersion.value) !== normalizeComparableVersion(localVersion)
})

const normalizeChapterVersionCount = (value: unknown): number => {
  const parsed = Number.parseInt(String(value ?? '').trim(), 10)
  if (!Number.isFinite(parsed)) {
    return MIN_CHAPTER_VERSION_COUNT
  }
  return Math.max(MIN_CHAPTER_VERSION_COUNT, Math.min(MAX_CHAPTER_VERSION_COUNT, parsed))
}

const normalizeConfigText = (value: unknown): string => String(value ?? '').trim()

const isHttpUrl = (value: string): boolean => {
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

interface RemoteVersionResponse {
  version?: string | null
  source?: string | null
  errors?: string[]
  build_time_beijing?: string | null
}

const resetVersionCheckResult = () => {
  remoteVersion.value = null
  remoteVersionSource.value = null
  remoteBuildTimeBeijing.value = null
  versionCheckError.value = null
}

const checkVersionSource = async (
  options: { silentIfInvalid?: boolean } = {},
) => {
  const { silentIfInvalid = false } = options
  const normalizedVersionInfoUrl = normalizeConfigText(versionInfoUrl.value)
  resetVersionCheckResult()

  if (!normalizedVersionInfoUrl) {
    if (!silentIfInvalid) {
      versionCheckError.value = '请先填写版本信息 JSON 地址'
    }
    return
  }

  if (!isHttpUrl(normalizedVersionInfoUrl)) {
    if (!silentIfInvalid) {
      versionCheckError.value = '版本信息 JSON 地址必须是 http/https URL'
    }
    return
  }

  versionCheckLoading.value = true
  try {
    const response = await fetch(`${API_BASE_URL}/api/updates/remote-version`, {
      method: 'GET'
    })
    if (!response.ok) {
      throw new Error(`请求失败，状态码: ${response.status}`)
    }

    const payload = await response.json() as RemoteVersionResponse
    const parsedVersion = typeof payload?.version === 'string'
      ? normalizeConfigText(payload.version)
      : ''
    remoteVersion.value = parsedVersion || null
    remoteVersionSource.value = typeof payload?.source === 'string'
      ? (normalizeConfigText(payload.source) || null)
      : null
    remoteBuildTimeBeijing.value = typeof payload?.build_time_beijing === 'string'
      ? (normalizeConfigText(payload.build_time_beijing) || null)
      : null

    if (!remoteVersion.value) {
      const errors = Array.isArray(payload?.errors)
        ? payload.errors.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
        : []
      versionCheckError.value = errors.length > 0
        ? `已请求版本源，但未解析到版本号：${errors.join('；')}`
        : '已请求版本源，但未解析到版本号'
    }
  } catch (err) {
    versionCheckError.value = err instanceof Error ? `版本解析失败：${err.message}` : '版本解析失败'
  } finally {
    versionCheckLoading.value = false
  }
}

const handleCheckVersionSource = () => {
  void checkVersionSource()
}

const syncChapterVersionCountFromConfigs = () => {
  const current = configs.value.find((item) => item.key === WRITER_VERSION_CONFIG_KEY)
  const legacy = configs.value.find((item) => item.key === LEGACY_WRITER_VERSION_CONFIG_KEY)
  const rawValue = current?.value ?? legacy?.value ?? String(MIN_CHAPTER_VERSION_COUNT)
  chapterVersionCount.value = normalizeChapterVersionCount(rawValue)
}

const syncVersionSourceFromConfigs = () => {
  const versionInfoConfig = configs.value.find((item) => item.key === VERSION_INFO_URL_CONFIG_KEY)
    || configs.value.find((item) => item.key === LEGACY_VERSION_INFO_URL_CONFIG_KEY)
  versionInfoUrl.value = normalizeConfigText(versionInfoConfig?.value)
}

const syncDerivedFormsFromConfigs = () => {
  syncChapterVersionCountFromConfigs()
  syncVersionSourceFromConfigs()
}

const fetchConfigs = async () => {
  configLoading.value = true
  configError.value = null
  try {
    configs.value = await AdminAPI.listSystemConfigs()
    syncDerivedFormsFromConfigs()
    void checkVersionSource({ silentIfInvalid: true })
  } catch (err) {
    configError.value = err instanceof Error ? err.message : '加载配置失败'
  } finally {
    configLoading.value = false
  }
}

const upsertConfigInList = (updated: SystemConfig) => {
  const index = configs.value.findIndex((item) => item.key === updated.key)
  if (index === -1) {
    configs.value.unshift(updated)
  } else {
    configs.value.splice(index, 1, updated)
  }
}

const saveChapterVersionCount = async () => {
  chapterVersionError.value = null
  chapterVersionSaving.value = true
  try {
    const normalized = normalizeChapterVersionCount(chapterVersionCount.value)
    chapterVersionCount.value = normalized
    const updated = await AdminAPI.upsertSystemConfig(WRITER_VERSION_CONFIG_KEY, {
      value: String(normalized),
      description: '每次生成章节的候选版本数量（支持 1~2）。'
    })
    upsertConfigInList(updated)
    showAlert('章节生成版本数已更新', 'success')
  } catch (err) {
    chapterVersionError.value = err instanceof Error ? err.message : '保存章节版本数失败'
    showAlert(chapterVersionError.value, 'error')
  } finally {
    chapterVersionSaving.value = false
  }
}

const saveVersionSources = async () => {
  versionSourceError.value = null
  const normalizedVersionInfoUrl = normalizeConfigText(versionInfoUrl.value)

  if (!normalizedVersionInfoUrl) {
    versionSourceError.value = '版本信息 JSON 地址不能为空'
    showAlert(versionSourceError.value, 'error')
    return
  }

  if (!isHttpUrl(normalizedVersionInfoUrl)) {
    versionSourceError.value = '版本信息 JSON 地址必须是 http/https URL'
    showAlert(versionSourceError.value, 'error')
    return
  }

  versionSourceSaving.value = true
  try {
    const infoConfigPayload: SystemConfigUpsertPayload = {
      value: normalizedVersionInfoUrl,
      description: '远程版本信息 JSON 地址，供 /api/updates/remote-version 优先读取。'
    }
    const infoUpdated = await AdminAPI.upsertSystemConfig(VERSION_INFO_URL_CONFIG_KEY, infoConfigPayload)
    upsertConfigInList(infoUpdated)

    syncVersionSourceFromConfigs()
    await checkVersionSource()
    if (remoteVersion.value) {
      const compareText = hasNewVersion.value
        ? `检测到远程版本 ${remoteVersion.value}，与本地 ${localVersion} 不一致`
        : `远程版本 ${remoteVersion.value} 与本地一致`
      showAlert(`版本检查地址已保存。${compareText}`, 'success')
    } else {
      showAlert('版本检查地址已保存，但未解析到远程版本号', 'info')
    }
  } catch (err) {
    versionSourceError.value = err instanceof Error ? err.message : '保存版本地址失败'
    showAlert(versionSourceError.value, 'error')
  } finally {
    versionSourceSaving.value = false
  }
}

const openCreateModal = () => {
  isCreateMode.value = true
  configForm.key = ''
  configForm.value = ''
  configForm.description = ''
  configModalVisible.value = true
}

const openEditModal = (config: SystemConfig) => {
  isCreateMode.value = false
  configForm.key = config.key
  configForm.value = config.value
  configForm.description = config.description || ''
  configModalVisible.value = true
}

const closeConfigModal = () => {
  configModalVisible.value = false
  configSaving.value = false
}

const submitConfig = async () => {
  const normalizedKey = configForm.key.trim()
  const normalizedValue = configForm.value.trim()

  if (!normalizedKey || !normalizedValue) {
    showAlert('Key 与 Value 均为必填项', 'error')
    return
  }

  if (
    normalizedKey === WRITER_VERSION_CONFIG_KEY
    || normalizedKey === LEGACY_WRITER_VERSION_CONFIG_KEY
  ) {
    const parsed = Number.parseInt(normalizedValue, 10)
    if (!Number.isFinite(parsed) || parsed < MIN_CHAPTER_VERSION_COUNT || parsed > MAX_CHAPTER_VERSION_COUNT) {
      showAlert('章节版本数仅支持设置为 1 或 2', 'error')
      return
    }
  }

  if (
    normalizedKey === VERSION_INFO_URL_CONFIG_KEY
    || normalizedKey === LEGACY_VERSION_INFO_URL_CONFIG_KEY
  ) {
    if (!isHttpUrl(normalizedValue)) {
      showAlert('版本地址必须是 http/https URL', 'error')
      return
    }
  }

  configSaving.value = true
  try {
    let updated: SystemConfig
    if (isCreateMode.value) {
      updated = await AdminAPI.upsertSystemConfig(normalizedKey, {
        value: normalizedValue,
        description: configForm.description || undefined
      })
      upsertConfigInList(updated)
    } else {
      updated = await AdminAPI.patchSystemConfig(configForm.key, {
        value: normalizedValue,
        description: configForm.description || undefined
      } as SystemConfigUpdatePayload)
      upsertConfigInList(updated)
    }
    syncDerivedFormsFromConfigs()
    void checkVersionSource({ silentIfInvalid: true })
    showAlert('配置已保存', 'success')
    closeConfigModal()
  } catch (err) {
    showAlert(err instanceof Error ? err.message : '保存失败', 'error')
  } finally {
    configSaving.value = false
  }
}

const deleteConfig = async (key: string) => {
  try {
    await AdminAPI.deleteSystemConfig(key)
    configs.value = configs.value.filter((item) => item.key !== key)
    syncDerivedFormsFromConfigs()
    void checkVersionSource({ silentIfInvalid: true })
    showAlert('配置已删除', 'success')
  } catch (err) {
    showAlert(err instanceof Error ? err.message : '删除失败', 'error')
  }
}

const columns: DataTableColumns<SystemConfig> = [
  {
    title: 'Key',
    key: 'key',
    width: 220,
    ellipsis: { tooltip: true }
  },
  {
    title: '值',
    key: 'value',
    ellipsis: { tooltip: true }
  },
  {
    title: '描述',
    key: 'description',
    ellipsis: { tooltip: true },
    render(row) {
      return row.description || '—'
    }
  },
  {
    title: '操作',
    key: 'actions',
    align: 'center',
    width: 160,
    render(row) {
      return h(
        NSpace,
        { justify: 'center', size: 'small' },
        {
          default: () => [
            h(
              NButton,
              {
                size: 'small',
                type: 'primary',
                tertiary: true,
                onClick: () => openEditModal(row)
              },
              { default: () => '编辑' }
            ),
            h(
              NPopconfirm,
              {
                'positive-text': '删除',
                'negative-text': '取消',
                type: 'error',
                placement: 'left',
                onPositiveClick: () => deleteConfig(row.key)
              },
              {
                default: () => '确认删除该配置项？',
                trigger: () =>
                  h(
                    NButton,
                    { size: 'small', type: 'error', quaternary: true },
                    { default: () => '删除' }
                  )
              }
            )
          ]
        }
      )
    }
  }
]

onMounted(() => {
  fetchConfigs()
})
</script>

<style scoped>
.admin-settings {
  width: 100%;
}

.top-settings-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.top-settings-card {
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.card-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: #1f2937;
}

.version-form {
  max-width: 540px;
}

.form-hint {
  margin: 2px 0 12px;
  color: #6b7280;
  font-size: 0.875rem;
}

.version-compare-panel {
  margin: 4px 0 12px;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f9fafb;
}

.compare-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.875rem;
  color: #374151;
  line-height: 1.6;
}

.compare-empty {
  color: #6b7280;
}

.compare-meta {
  margin-top: 4px;
  font-size: 0.8125rem;
  color: #6b7280;
}

.compare-result {
  margin-top: 8px;
  font-size: 0.875rem;
  font-weight: 500;
}

.compare-new {
  color: #b45309;
}

.compare-same {
  color: #047857;
}

.compare-error {
  color: #b91c1c;
}

.config-modal {
  max-width: min(640px, 92vw);
}

@media (max-width: 767px) {
  .top-settings-grid {
    grid-template-columns: 1fr;
  }

  .card-title {
    font-size: 1.125rem;
  }
}
</style>
