// API 客户端：基于 fetch 的轻量封装
import { reactive } from 'vue'
import type {
  AccessEvent,
  Alert,
  BridgeStatus,
  CalibrateResult,
  Device,
  FaceStatus,
  FrameResult,
  Permission,
  Summary,
  User,
  EdgeStatus,
  SampleRole,
  ImageSampleResult,
} from './types'

const BASE = '' // 开发环境经 vite proxy 转发到 8000

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(BASE + path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  edgeStatus: () => req<EdgeStatus>('GET', '/api/edge/status'),
  edgeEnroll: (user_id: number | null, role: 'train' | 'validation' | 'unknown') =>
    req<{ ok: boolean; message: string; request_id: string }>('POST', '/api/edge/enroll', { user_id, role }),
  edgeCalibrate: () => req<{ ok: boolean; message: string; details: { name: string; enabled: boolean; message: string }[] }>('POST', '/api/edge/calibrate'),
  edgeImageSample: (user_id: number | null, role: SampleRole, image: string, source: 'upload' | 'webcam') =>
    req<ImageSampleResult>('POST', '/api/edge/image-sample', { user_id, role, image, source }),
  // 设备
  devices: () => req<Device[]>('GET', '/api/devices'),
  deviceStatus: () => req<BridgeStatus>('GET', '/api/devices/status'),
  switchMode: (body: { mode: string; com_port?: string; baud?: number }) =>
    req<BridgeStatus>('POST', '/api/devices/mode', body),

  // 人员
  users: () => req<User[]>('GET', '/api/users'),
  createUser: (body: { user_no: string; name: string }) => req<User>('POST', '/api/users', body),
  updateUser: (id: number, body: { name?: string; enabled?: boolean }) =>
    req<User>('PUT', `/api/users/${id}`, body),
  deleteUser: (id: number) => req<void>('DELETE', `/api/users/${id}`),

  // 人脸
  faceSample: (user_id: number, image: string) =>
    req<{ ok: boolean; message: string; sample_count?: number }>('POST', '/api/faces/sample', {
      user_id,
      image,
    }),
  faceTrain: () =>
    req<{ ok: boolean; message: string; samples?: number; model_version?: string | null }>(
      'POST',
      '/api/faces/train',
    ),
  faceCalibrate: () => req<CalibrateResult>('POST', '/api/faces/calibrate'),
  faceValidate: (user_id: number, image: string) =>
    req<{ verdict: string; recognition: string; confidence: number | null; message: string }>(
      'POST',
      '/api/faces/validate',
      { user_id, image },
    ),
  faceStatus: () => req<FaceStatus>('GET', '/api/faces/status'),

  // 权限
  permissions: (userId?: number) =>
    req<Permission[]>('GET', `/api/permissions${userId ? `?user_id=${userId}` : ''}`),
  createPermission: (body: {
    user_id: number
    device_id: number
    valid_from: string
    valid_to: string
  }) => req<Permission>('POST', '/api/permissions', body),
  updatePermission: (id: number, body: { enabled?: boolean }) =>
    req<Permission>('PUT', `/api/permissions/${id}`, body),
  deletePermission: (id: number) => req<void>('DELETE', `/api/permissions/${id}`),

  // 通行
  processFrame: (image: string, device_no = 'D001') =>
    req<FrameResult>('POST', '/api/access/frame', { image, device_no }),
  events: (params: { limit?: number; authorization?: string; recognition?: string } = {}) => {
    const q = new URLSearchParams()
    if (params.limit) q.set('limit', String(params.limit))
    if (params.authorization) q.set('authorization', params.authorization)
    if (params.recognition) q.set('recognition', params.recognition)
    return req<AccessEvent[]>('GET', `/api/access/events?${q.toString()}`)
  },
  summary: () => req<Summary>('GET', '/api/access/summary'),

  // 告警
  alerts: (params: { limit?: number; resolved?: boolean } = {}) => {
    const q = new URLSearchParams()
    if (params.limit) q.set('limit', String(params.limit))
    if (params.resolved !== undefined) q.set('resolved', String(params.resolved))
    return req<Alert[]>('GET', `/api/alerts?${q.toString()}`)
  },
  resolveAlert: (id: number) => req<Alert>('PUT', `/api/alerts/${id}`, { resolved: true }),
}

// 共享响应式状态（简单 store，替代 pinia）
export const store = reactive({
  status: null as BridgeStatus | null,
  edge: null as EdgeStatus | null,
  summary: null as Summary | null,
  events: [] as AccessEvent[],
  alerts: [] as Alert[],
  users: [] as User[],
  devices: [] as Device[],
  face: null as FaceStatus | null,
  loading: false,
  error: '' as string,
})

export async function refreshStatus() {
  try {
    const [status, summary, events, alerts, edge] = await Promise.all([
      api.deviceStatus(),
      api.summary(),
      api.events({ limit: 10 }),
      api.alerts({ limit: 8 }),
      api.edgeStatus(),
    ])
    store.status = status
    store.summary = summary
    store.events = events
    store.alerts = alerts
    store.edge = edge
    store.error = ''
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

export async function refreshUsers() {
  store.users = await api.users()
}

export async function refreshFace() {
  store.face = await api.faceStatus()
}

export function cameraFrameToBase64(video: HTMLVideoElement, quality = 0.85): string {
  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth || 640
  canvas.height = video.videoHeight || 480
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/jpeg', quality).split(',')[1]
}

export function formatDT(dt: string): string {
  // "YYYY-MM-DDTHH:MM"（datetime-local）→ "YYYY-MM-DD HH:MM"
  return dt.replace('T', ' ').slice(0, 16)
}
