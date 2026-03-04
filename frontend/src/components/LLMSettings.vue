<!-- AIMETA P=LLM设置_模型配置界面|R=LLM配置表单|NR=不含模型调用|E=component:LLMSettings|X=internal|A=设置组件|D=vue|S=dom,net|RD=./README.ai -->
<template>
  <div class="bg-white/70 backdrop-blur-xl rounded-2xl shadow-lg p-8">
    <h2 class="text-2xl font-bold text-gray-800 mb-6">LLM 配置</h2>
    <h5 class="text-1xl font-bold text-gray-800 mb-6">建议使用自己的中转API和KEY</h5>
    <form @submit.prevent="handleSave" class="space-y-6">
      <div>
        <label for="url" class="block text-sm font-medium text-gray-700">API URL</label>
        <div class="relative mt-1">
          <input
            type="text"
            id="url"
            v-model="config.llm_provider_url"
            class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            placeholder="https://api.example.com/v1"
          >
          <button
            type="button"
            @click="clearApiUrl"
            class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
            aria-label="清空 API URL"
          >
            <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
          </button>
        </div>
      </div>
      <div>
        <label for="key" class="block text-sm font-medium text-gray-700">API Key</label>
        <div class="relative mt-1">
          <input
            :type="showApiKey ? 'text' : 'password'"
            id="key"
            v-model="config.llm_provider_api_key"
            class="block w-full px-3 py-2 pr-24 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            placeholder="留空则使用默认Key"
          >
          <button
            type="button"
            @click="clearApiKey"
            class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
            aria-label="清空 API Key"
          >
            <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
          </button>
          <button
            type="button"
            @click="toggleApiKeyVisibility"
            class="absolute inset-y-0 right-10 flex items-center px-2 text-gray-400 hover:text-gray-600"
            :aria-label="showApiKey ? '隐藏 API Key' : '显示 API Key'"
          >
            <svg v-if="showApiKey" class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 5c-4.478 0-8.268 2.943-9.542 7C1.732 16.057 5.522 19 10 19s8.268-2.943 9.542-7C18.268 7.943 14.478 5 10 5zm0 10a5 5 0 110-10 5 5 0 010 10z" fill-opacity="0.2" />
              <path d="M10 7a3 3 0 100 6 3 3 0 000-6z" />
            </svg>
            <svg v-else class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zm13.707 0a4.167 4.167 0 11-8.334 0 4.167 4.167 0 018.334 0z" clip-rule="evenodd" />
              <path d="M10 8a2 2 0 100 4 2 2 0 000-4z" />
            </svg>
          </button>
        </div>
      </div>
      <div>
        <label for="model" class="block text-sm font-medium text-gray-700">Model</label>
        <div class="flex gap-2 mt-1">
          <div class="relative flex-1">
            <input
              type="text"
              id="model"
              v-model="config.llm_provider_model"
              @focus="handleModelFocus"
              @blur="hideDropdown"
              class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              placeholder="留空则使用默认模型"
            >
            <button
              type="button"
              @click="clearApiModel"
              class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
              aria-label="清空模型名称"
            >
              <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
              </svg>
            </button>
            <!-- 下拉选择提示框 -->
            <div
              v-if="showModelDropdown && availableModels.length > 0"
              class="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-md shadow-lg max-h-60 overflow-auto"
            >
              <div
                v-for="model in filteredModels"
                :key="model"
                @mousedown="selectModel(model)"
                class="px-3 py-2 cursor-pointer hover:bg-indigo-50 hover:text-indigo-600 text-sm"
              >
                {{ model }}
              </div>
              <div v-if="filteredModels.length === 0" class="px-3 py-2 text-sm text-gray-500">
                无匹配的模型
              </div>
            </div>
          </div>
          <button
            type="button"
            @click="manualTryLoadModels"
            :disabled="isLoadingModels"
            class="px-4 py-2 bg-gray-600 text-white rounded-md hover:bg-gray-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <svg v-if="isLoadingModels" class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>{{ isLoadingModels ? '加载中...' : '获取模型' }}</span>
          </button>
          <button
            type="button"
            @click="retryLoadModels"
            :disabled="isLoadingModels"
            class="px-4 py-2 bg-amber-600 text-white rounded-md hover:bg-amber-700 transition-colors disabled:bg-amber-400 disabled:cursor-not-allowed"
          >
            重试
          </button>
        </div>
        <p v-if="lastLoadError" class="mt-2 text-sm text-red-600">{{ lastLoadError }}</p>
      </div>
      <div class="border border-gray-200 rounded-xl p-4 space-y-4">
        <h3 class="text-base font-semibold text-gray-800">向量模型配置</h3>
        <label class="flex items-center gap-2 text-sm text-gray-700">
          <input
            v-model="useMainUrlForEmbedding"
            type="checkbox"
            class="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          >
          复用主模型 API URL
        </label>
        <div v-if="!useMainUrlForEmbedding">
          <label for="embedding-url" class="block text-sm font-medium text-gray-700">向量 API URL</label>
          <div class="relative mt-1">
            <input
              id="embedding-url"
              v-model="config.embedding_provider_url"
              type="text"
              class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              placeholder="https://api.example.com/v1"
            >
            <button
              type="button"
              @click="clearEmbeddingUrl"
              class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
              aria-label="清空向量 API URL"
            >
              <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
              </svg>
            </button>
          </div>
        </div>
        <div>
          <label for="embedding-model" class="block text-sm font-medium text-gray-700">向量 Model</label>
          <div class="flex gap-2 mt-1">
            <div class="relative flex-1">
              <input
                id="embedding-model"
                v-model="config.embedding_provider_model"
                type="text"
                @focus="handleEmbeddingModelFocus"
                @blur="hideEmbeddingDropdown"
                class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                placeholder="留空则使用系统默认向量模型"
              >
              <button
                type="button"
                @click="clearEmbeddingModel"
                class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
                aria-label="清空向量模型名称"
              >
                <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
                </svg>
              </button>
              <div
                v-if="showEmbeddingModelDropdown && availableEmbeddingModels.length > 0"
                class="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-md shadow-lg max-h-60 overflow-auto"
              >
                <div
                  v-for="model in filteredEmbeddingModels"
                  :key="model"
                  @mousedown="selectEmbeddingModel(model)"
                  class="px-3 py-2 cursor-pointer hover:bg-indigo-50 hover:text-indigo-600 text-sm"
                >
                  {{ model }}
                </div>
                <div v-if="filteredEmbeddingModels.length === 0" class="px-3 py-2 text-sm text-gray-500">
                  无匹配的模型
                </div>
              </div>
            </div>
            <button
              type="button"
              @click="manualTryLoadEmbeddingModels"
              :disabled="isLoadingEmbeddingModels"
              class="px-4 py-2 bg-gray-600 text-white rounded-md hover:bg-gray-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
            >
              <svg v-if="isLoadingEmbeddingModels" class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span>{{ isLoadingEmbeddingModels ? '加载中...' : '获取模型' }}</span>
            </button>
            <button
              type="button"
              @click="retryLoadEmbeddingModels"
              :disabled="isLoadingEmbeddingModels"
              class="px-4 py-2 bg-amber-600 text-white rounded-md hover:bg-amber-700 transition-colors disabled:bg-amber-400 disabled:cursor-not-allowed"
            >
              重试
            </button>
          </div>
          <p v-if="lastEmbeddingLoadError" class="mt-2 text-sm text-red-600">{{ lastEmbeddingLoadError }}</p>
        </div>
      </div>
      <div class="flex justify-end space-x-4 pt-4">
        <button type="button" @click="handleDelete" class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors">删除配置</button>
        <button type="submit" class="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">保存</button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { getLLMConfig, createOrUpdateLLMConfig, deleteLLMConfig, getAvailableModels, type LLMConfigCreate } from '@/api/llm';

interface LLMSettingsForm {
  llm_provider_url: string;
  llm_provider_api_key: string;
  llm_provider_model: string;
  embedding_provider_url: string;
  embedding_provider_model: string;
}

const createEmptyConfig = (): LLMSettingsForm => ({
  llm_provider_url: '',
  llm_provider_api_key: '',
  llm_provider_model: '',
  embedding_provider_url: '',
  embedding_provider_model: '',
});

const config = ref<LLMSettingsForm>(createEmptyConfig());
const useMainUrlForEmbedding = ref(true);

const showApiKey = ref(false);
const availableModels = ref<string[]>([]);
const isLoadingModels = ref(false);
const showModelDropdown = ref(false);
const lastLoadError = ref('');
const availableEmbeddingModels = ref<string[]>([]);
const isLoadingEmbeddingModels = ref(false);
const showEmbeddingModelDropdown = ref(false);
const lastEmbeddingLoadError = ref('');

// 根据输入过滤模型列表
const filteredModels = computed(() => {
  if (!config.value.llm_provider_model) {
    return availableModels.value;
  }
  const searchTerm = config.value.llm_provider_model.toLowerCase();
  return availableModels.value.filter(model =>
    model.toLowerCase().includes(searchTerm)
  );
});

const filteredEmbeddingModels = computed(() => {
  if (!config.value.embedding_provider_model) {
    return availableEmbeddingModels.value;
  }
  const searchTerm = config.value.embedding_provider_model.toLowerCase();
  return availableEmbeddingModels.value.filter(model =>
    model.toLowerCase().includes(searchTerm)
  );
});

onMounted(async () => {
  const existingConfig = await getLLMConfig();
  if (existingConfig) {
    config.value = {
      llm_provider_url: existingConfig.llm_provider_url || '',
      llm_provider_api_key: existingConfig.llm_provider_api_key || '',
      llm_provider_model: existingConfig.llm_provider_model || '',
      embedding_provider_url: existingConfig.embedding_provider_url || '',
      embedding_provider_model: existingConfig.embedding_provider_model || '',
    };
    useMainUrlForEmbedding.value = !existingConfig.embedding_provider_url;
  }
});

const buildPayload = (): LLMConfigCreate => ({
  llm_provider_url: config.value.llm_provider_url.trim() || null,
  llm_provider_api_key: config.value.llm_provider_api_key.trim() || null,
  llm_provider_model: config.value.llm_provider_model.trim() || null,
  embedding_provider_url: useMainUrlForEmbedding.value
    ? null
    : (config.value.embedding_provider_url.trim() || null),
  embedding_provider_model: config.value.embedding_provider_model.trim() || null,
});

const handleSave = async () => {
  await createOrUpdateLLMConfig(buildPayload());
  alert('设置已保存！');
};

const handleDelete = async () => {
  if (confirm('确定要删除您的自定义LLM配置吗？删除后将恢复为默认配置。')) {
    await deleteLLMConfig();
    config.value = createEmptyConfig();
    useMainUrlForEmbedding.value = true;
    availableModels.value = [];
    availableEmbeddingModels.value = [];
    alert('配置已删除！');
  }
};

const toggleApiKeyVisibility = () => {
  showApiKey.value = !showApiKey.value;
};

const clearApiKey = () => {
  config.value.llm_provider_api_key = '';
};

const clearApiUrl = () => {
  config.value.llm_provider_url = '';
};

const clearApiModel = () => {
  config.value.llm_provider_model = '';
};

const clearEmbeddingUrl = () => {
  config.value.embedding_provider_url = '';
};

const clearEmbeddingModel = () => {
  config.value.embedding_provider_model = '';
};

const getEffectiveEmbeddingUrl = (): string => (
  useMainUrlForEmbedding.value
    ? config.value.llm_provider_url.trim()
    : config.value.embedding_provider_url.trim()
);

const normalizeModelsEndpoint = (rawUrl: string): string => {
  const trimmed = rawUrl.trim().replace(/\/+$/, '');
  if (!trimmed) {
    return '';
  }
  if (trimmed.endsWith('/v1/models')) {
    return trimmed;
  }
  if (trimmed.endsWith('/v1')) {
    return `${trimmed}/models`;
  }
  if (trimmed.endsWith('/models')) {
    const withoutModels = trimmed.slice(0, -'/models'.length).replace(/\/+$/, '');
    return `${withoutModels}/v1/models`;
  }
  return `${trimmed}/v1/models`;
};

const extractApiErrorMessage = (payload: unknown): string => {
  if (!payload || typeof payload !== 'object') {
    return '';
  }
  const error = (payload as { error?: unknown }).error;
  if (!error || typeof error !== 'object') {
    return '';
  }
  const message = (error as { message?: unknown }).message;
  return typeof message === 'string' ? message : '';
};

const isLikelyBrowserNetworkError = (error: unknown): boolean => {
  if (!(error instanceof Error)) {
    return false;
  }
  const message = error.message.toLowerCase();
  return (
    error.name === 'TypeError'
    || message.includes('failed to fetch')
    || message.includes('networkerror')
    || message.includes('net::err_failed')
    || message.includes('cors')
  );
};

const fetchOpenAIModels = async (apiKey: string, apiUrl: string): Promise<string[]> => {
  const endpoint = normalizeModelsEndpoint(apiUrl);
  if (!endpoint) {
    throw new Error('请先填写 API URL');
  }

  const response = await fetch(endpoint, {
    method: 'GET',
    cache: 'no-store',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
  });

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const apiErrorMessage = extractApiErrorMessage(payload);
    throw new Error(apiErrorMessage || `请求失败 (HTTP ${response.status})`);
  }

  const data = (payload as { data?: unknown[] } | null)?.data;
  if (!Array.isArray(data)) {
    return [];
  }

  const modelIds = data
    .map((item) => (item && typeof item === 'object' ? (item as { id?: unknown }).id : undefined))
    .filter((id): id is string => typeof id === 'string' && id.length > 0);

  return modelIds.sort((a, b) => a.localeCompare(b));
};

const fetchModelsViaBackend = async (apiKey: string, apiUrl: string): Promise<string[]> => {
  const models = await getAvailableModels({
    llm_provider_api_key: apiKey,
    llm_provider_url: apiUrl,
  });

  if (!Array.isArray(models)) {
    return [];
  }

  return models
    .filter((model): model is string => typeof model === 'string' && model.length > 0)
    .sort((a, b) => a.localeCompare(b));
};

const fetchModelsWithFallback = async (apiKey: string, apiUrl: string): Promise<string[]> => {
  try {
    return await fetchOpenAIModels(apiKey, apiUrl);
  } catch (frontendError) {
    if (!isLikelyBrowserNetworkError(frontendError)) {
      throw frontendError;
    }

    const backendModels = await fetchModelsViaBackend(apiKey, apiUrl);
    if (backendModels.length > 0) {
      return backendModels;
    }

    throw frontendError;
  }
};

const loadModels = async () => {
  if (isLoadingModels.value) {
    return;
  }

  const apiKey = config.value.llm_provider_api_key?.trim() || '';
  const apiUrl = config.value.llm_provider_url?.trim() || '';

  // 验证表单
  if (!apiKey) {
    alert('请先填写 API Key');
    return;
  }
  if (!apiUrl) {
    alert('请先填写 API URL');
    return;
  }

  isLoadingModels.value = true;
  lastLoadError.value = '';
  try {
    const models = await fetchModelsWithFallback(apiKey, apiUrl);
    availableModels.value = models;
    if (models.length > 0) {
      showModelDropdown.value = true;
    } else {
      alert('未获取到模型列表，请检查 API URL 和 API Key 是否正确');
    }
  } catch (error) {
    console.error('Failed to load models:', error);
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    lastLoadError.value = `获取模型列表失败：${errorMessage}`;
    alert(`获取模型列表失败：${errorMessage}`);
  } finally {
    isLoadingModels.value = false;
  }
};

const manualTryLoadModels = async () => {
  await loadModels();
};

const loadEmbeddingModels = async () => {
  if (isLoadingEmbeddingModels.value) {
    return;
  }

  const apiKey = config.value.llm_provider_api_key.trim();
  const apiUrl = getEffectiveEmbeddingUrl();

  if (!apiKey) {
    alert('请先填写 API Key');
    return;
  }
  if (!apiUrl) {
    alert(useMainUrlForEmbedding.value ? '请先填写主模型 API URL' : '请先填写向量 API URL');
    return;
  }

  isLoadingEmbeddingModels.value = true;
  lastEmbeddingLoadError.value = '';
  try {
    const models = await fetchModelsWithFallback(apiKey, apiUrl);
    availableEmbeddingModels.value = models;
    if (models.length > 0) {
      showEmbeddingModelDropdown.value = true;
    } else {
      alert('未获取到向量模型列表，请检查 API URL 和 API Key 是否正确');
    }
  } catch (error) {
    console.error('Failed to load embedding models:', error);
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    lastEmbeddingLoadError.value = `获取向量模型列表失败：${errorMessage}`;
    alert(`获取向量模型列表失败：${errorMessage}`);
  } finally {
    isLoadingEmbeddingModels.value = false;
  }
};

const retryLoadModels = async () => {
  await loadModels();
};

const manualTryLoadEmbeddingModels = async () => {
  await loadEmbeddingModels();
};

const retryLoadEmbeddingModels = async () => {
  await loadEmbeddingModels();
};

const handleModelFocus = async () => {
  showModelDropdown.value = true;
  if (availableModels.value.length === 0 && config.value.llm_provider_api_key) {
    await loadModels();
  }
};

const handleEmbeddingModelFocus = async () => {
  showEmbeddingModelDropdown.value = true;
  if (availableEmbeddingModels.value.length === 0 && config.value.llm_provider_api_key) {
    await loadEmbeddingModels();
  }
};

const selectModel = (model: string) => {
  config.value.llm_provider_model = model;
  showModelDropdown.value = false;
};

const selectEmbeddingModel = (model: string) => {
  config.value.embedding_provider_model = model;
  showEmbeddingModelDropdown.value = false;
};

const hideDropdown = () => {
  // 延迟隐藏，确保点击事件能触发
  setTimeout(() => {
    showModelDropdown.value = false;
  }, 200);
};

const hideEmbeddingDropdown = () => {
  setTimeout(() => {
    showEmbeddingModelDropdown.value = false;
  }, 200);
};
</script>
