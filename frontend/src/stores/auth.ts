// AIMETA P=认证状态_用户登录状态管理|R=token_user_login_logout|NR=不含API调用|E=store:auth|X=internal|A=useAuthStore|D=pinia|S=storage|RD=./README.ai
import { defineStore } from 'pinia';
import { API_BASE_URL } from '@/api/novel';

const API_URL = `${API_BASE_URL}/api/auth`;

interface AuthOptions {
  // 是否允许用户自助注册
  allow_registration: boolean;
  // 是否启用 Linux.do 登录
  enable_linuxdo_login: boolean;
}

interface FetchWithAuthOptions extends RequestInit {
  timeoutMs?: number;
  debugTag?: string;
}

// Helper function to handle fetch requests and token refreshing
async function fetchWithAuth(url: string, options: FetchWithAuthOptions = {}) {
  const { timeoutMs = 15000, debugTag = 'request', ...requestOptions } = options;
  const authStore = useAuthStore();
  const method = String(requestOptions.method || 'GET').toUpperCase();
  const headers = new Headers(requestOptions.headers || {});
  const controller = new AbortController();
  const startedAt = performance.now();
  const timeoutId = window.setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  console.log(`[auth:${debugTag}] -> ${method} ${url}`, {
    hasToken: Boolean(authStore.token),
    timeoutMs,
  });
  
  if (authStore.token) {
    headers.set('Authorization', `Bearer ${authStore.token}`);
  }

  try {
    const response = await fetch(url, {
      ...requestOptions,
      headers,
      signal: controller.signal,
    });

    console.log(`[auth:${debugTag}] <- ${method} ${url}`, {
      status: response.status,
      ok: response.ok,
      durationMs: Math.round(performance.now() - startedAt),
    });

    const refreshedToken = response.headers.get('X-Token-Refresh');
    if (refreshedToken) {
      authStore.token = refreshedToken;
      localStorage.setItem('token', refreshedToken);
      console.log(`[auth:${debugTag}] token refreshed`);
    }

    return response;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      const timeoutError = new Error(`Request timed out after ${timeoutMs}ms: ${method} ${url}`);
      console.error(`[auth:${debugTag}] timeout`, timeoutError);
      throw timeoutError;
    }
    console.error(`[auth:${debugTag}] request failed`, error);
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

interface User {
  id: number;
  username: string;
  is_admin: boolean;
  must_change_password: boolean;
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || null as string | null,
    user: null as User | null,
    authOptions: null as AuthOptions | null,
    authOptionsLoaded: false,
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
    allowRegistration: (state) => state.authOptions?.allow_registration ?? true,
    enableLinuxdoLogin: (state) => state.authOptions?.enable_linuxdo_login ?? false,
    mustChangePassword: (state) => state.user?.must_change_password ?? false,
  },
  actions: {
    async fetchAuthOptions(force = false) {
      // 拉取后端认证相关开关，供前端动态渲染
      if (this.authOptionsLoaded && !force) {
        return;
      }
      try {
        const response = await fetch(`${API_URL}/options`);
        if (!response.ok) {
          throw new Error('读取认证开关失败');
        }
        const data = await response.json() as AuthOptions;
        this.authOptions = data;
      } catch (error) {
        console.error('获取认证配置失败，将使用默认值', error);
        this.authOptions = {
          allow_registration: true,
          enable_linuxdo_login: false,
        };
      } finally {
        this.authOptionsLoaded = true;
      }
    },
    async login(username: string, password: string): Promise<boolean> {
      const params = new URLSearchParams();
      params.append('grant_type', 'password');
      params.append('username', username);
      params.append('password', password);

      const response = await fetchWithAuth(`${API_URL}/token`, {
        method: 'POST',
        body: params,
        timeoutMs: 15000,
        debugTag: 'login/token',
      });

      if (!response.ok) {
        throw new Error('Failed to login');
      }

      const data = await response.json();
      if (!data?.access_token) {
        throw new Error('Missing access token in login response');
      }
      const accessToken = String(data.access_token);
      this.token = accessToken;
      localStorage.setItem('token', accessToken);
      const mustChangePassword = Boolean(data.must_change_password);
      try {
        await this.fetchUser({
          logoutOnFailure: false,
          timeoutMs: 10000,
          debugTag: 'login/users-me',
        });
      } catch (error) {
        this.logout();
        throw new Error('Failed to initialize user session');
      }
      if (!this.user) {
        this.logout();
        throw new Error('Failed to initialize user session');
      }
      this.user.must_change_password = mustChangePassword || this.user.must_change_password;
      return mustChangePassword;
    },
    // 当前注册流程在 Register.vue 中实现，此处预留方法以兼容旧逻辑
    async register(payload: { username: string; email: string; password: string; verification_code: string }) {
      const response = await fetch(`${API_URL}/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const detail = errorData.detail || 'Failed to register';
        throw new Error(detail);
      }
    },
    logout() {
      this.token = null;
      this.user = null;
      localStorage.removeItem('token');
    },
    async fetchUser(options: { logoutOnFailure?: boolean; timeoutMs?: number; debugTag?: string } = {}): Promise<User | null> {
      if (!this.token) {
        this.user = null;
        return null;
      }
      const { logoutOnFailure = true } = options;
      try {
        const response = await fetchWithAuth(`${API_URL}/users/me`, {
          timeoutMs: options.timeoutMs ?? 10000,
          debugTag: options.debugTag ?? 'fetchUser/me',
        });

        if (!response.ok) {
          throw new Error('Failed to fetch user');
        }

        const userData = await response.json();
        this.user = {
          id: userData.id,
          username: userData.username,
          is_admin: userData.is_admin || false,
          must_change_password: userData.must_change_password || false,
        };
        return this.user;
      } catch (error) {
        if (logoutOnFailure) {
          this.logout();
        }
        throw error;
      }
    },
  },
});
