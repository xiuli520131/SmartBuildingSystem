<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, cameraFrameToBase64, refreshStatus, store } from '../api'
import type { FrameResult } from '../types'

const video = ref<HTMLVideoElement>()
const cameraOn = ref(false)
const cameraError = ref('')
const submitting = ref(false)
const lastResult = ref<FrameResult | null>(null)
let stream: MediaStream | null = null
let frameTimer: number | undefined
let busy = false

// 桥接模式切换
const comPort = ref('COM9')
const baud = ref(115200)
const switching = ref(false)
const switchError = ref('')

async function switchToSerial() {
  switching.value = true
  switchError.value = ''
  try {
    await api.switchMode({ mode: 'serial', com_port: comPort.value, baud: baud.value })
    await refreshStatus()
  } catch (e) {
    switchError.value = '切换失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    switching.value = false
  }
}

async function switchToMock() {
  switching.value = true
  switchError.value = ''
  try {
    await api.switchMode({ mode: 'mock' })
    await refreshStatus()
  } catch (e) {
    switchError.value = '切回模拟失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    switching.value = false
  }
}

const RECOG_LABEL: Record<string, string> = {
  no_face: '未检测到人脸',
  multiple: '检测到多张人脸',
  unknown: '未知人员',
  recognized: '已识别',
  error: '识别异常',
}

const AUTH_LABEL: Record<string, string> = {
  allow: '允许',
  deny: '拒绝',
  block: '拦截',
  none: '—',
}

const EXEC_LABEL: Record<string, string> = {
  pending: '等待板端确认',
  confirmed: '板端已确认',
  timeout: '未确认（超时）',
  error: '板端错误',
  unconfirmed: '未确认',
  not_sent: '未发送',
}

async function startCamera() {
  cameraError.value = ''
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    })
    if (video.value) {
      video.value.srcObject = stream
      await video.value.play()
    }
    cameraOn.value = true
    frameTimer = window.setInterval(submitFrame, 1500)
  } catch (e) {
    cameraError.value =
      '无法打开摄像头：' +
      (e instanceof DOMException && e.name === 'NotAllowedError'
        ? '浏览器未授权摄像头权限'
        : e instanceof Error
          ? e.message
          : String(e))
  }
}

function stopCamera() {
  if (frameTimer) {
    window.clearInterval(frameTimer)
    frameTimer = undefined
  }
  if (stream) {
    stream.getTracks().forEach((t) => t.stop())
    stream = null
  }
  cameraOn.value = false
  if (video.value) video.value.srcObject = null
}

async function submitFrame() {
  if (busy || !cameraOn.value || !video.value || !video.value.videoWidth) return
  busy = true
  submitting.value = true
  try {
    const b64 = cameraFrameToBase64(video.value)
    lastResult.value = await api.processFrame(b64)
  } catch (e) {
    lastResult.value = {
      ok: false,
      message: e instanceof Error ? e.message : String(e),
      recognition: 'error',
      confidence: null,
      recognized_no: null,
      recognized_name: null,
      authorization: 'block',
      auth_reason: '',
      request_id: null,
      execution: null,
      raw: null,
      device_online: false,
      device_lock_state: '',
      event_id: null,
    }
  } finally {
    busy = false
    submitting.value = false
  }
}

async function captureOnce() {
  if (!video.value || !video.value.videoWidth) return
  busy = true
  try {
    lastResult.value = await api.processFrame(cameraFrameToBase64(video.value))
    refreshStatus()
  } catch (e) {
    lastResult.value = null
  } finally {
    busy = false
  }
}

function recBadgeClass(r: string): string {
  if (r === 'recognized') return 'green'
  if (r === 'unknown') return 'amber'
  if (r === 'no_face') return 'gray'
  if (r === 'multiple') return 'amber'
  return 'red'
}

function authBadgeClass(a: string): string {
  if (a === 'allow') return 'green'
  if (a === 'deny') return 'red'
  if (a === 'block') return 'amber'
  return 'gray'
}

onMounted(() => {
  refreshStatus()
})

onUnmounted(() => {
  stopCamera()
})
</script>

<template>
  <div class="page">
    <div class="cols-2 grid">
      <!-- 左：摄像头与识别结果 -->
      <div v-if="store.edge?.input_mode !== 'legacy'" class="panel">
        <h2>板载摄像头识别 <span class="sub">开发板提取特征 · 电脑匹配身份与权限</span></h2>
        <div class="result-banner">
          <span class="badge" :class="store.edge?.ready ? 'green' : 'amber'">
            {{ store.edge?.ready ? '板端特征模块就绪' : '等待板端特征固件连接' }}
          </span>
          <p>来访人员面对开发板摄像头。网页接收板端结果，电脑摄像头不参与通行。</p>
          <p>摄像头{{ store.status?.online ? '状态' : '最近上报状态（当前离线）' }}：{{ store.edge?.camera_state || '等待连接' }}</p>
          <p v-if="store.edge?.last_result">{{ store.edge.last_result.message }}</p>
          <p v-if="store.edge?.last_result?.recognized_name">人员：{{ store.edge.last_result.recognized_name }}</p>
          <p v-if="store.edge?.last_result?.distance != null">匹配距离：{{ store.edge.last_result.distance.toFixed(1) }}</p>
          <p v-if="store.edge?.last_result?.execution">执行：{{ EXEC_LABEL[store.edge.last_result.execution] || store.edge.last_result.execution }}</p>
          <p v-if="store.edge?.transport_error" class="banner-error">{{ store.edge.transport_error }}</p>
        </div>
        <div class="muted" style="margin-top: 16px">
          当前仅使用 LED 模拟解锁。人员录入请前往「人员与权限」。板载视频预览暂未提供。
        </div>
      </div>
      <div v-else class="panel">
        <h2>
          摄像头识别
          <span class="sub">浏览器 getUserMedia · 每 1.5 秒提交一帧</span>
        </h2>
        <div class="video-box">
          <video v-show="cameraOn" ref="video" muted playsinline></video>
          <div v-if="!cameraOn" class="placeholder">
            <span>摄像头未开启</span>
          </div>
          <div v-if="lastResult" class="video-overlay">
            <span class="badge" :class="recBadgeClass(lastResult.recognition)">
              {{ RECOG_LABEL[lastResult.recognition] || lastResult.recognition }}
            </span>
            <span v-if="lastResult.recognized_name" class="badge blue">
              {{ lastResult.recognized_name }}（{{ lastResult.recognized_no }}）
            </span>
            <span v-if="lastResult.confidence !== null && lastResult.confidence !== undefined" class="muted">
              距离 {{ lastResult.confidence.toFixed(1) }}
            </span>
          </div>
        </div>
        <div class="toolbar" style="margin-top: 12px; margin-bottom: 0">
          <button v-if="!cameraOn" class="btn primary" @click="startCamera">开启摄像头</button>
          <button v-else class="btn danger" @click="stopCamera">停止识别</button>
          <button v-if="cameraOn" class="btn" @click="captureOnce" :disabled="busy">
            立即识别一次
          </button>
          <span v-if="submitting" class="muted">提交中…</span>
        </div>
        <div v-if="cameraError" class="banner-error">{{ cameraError }}</div>

        <div v-if="lastResult" class="result-banner">
          <div class="row">
            <span class="badge" :class="authBadgeClass(lastResult.authorization)">
              {{ AUTH_LABEL[lastResult.authorization] || lastResult.authorization }}
            </span>
            <span>{{ lastResult.auth_reason || lastResult.message }}</span>
          </div>
          <div class="row muted" style="margin-top: 6px; font-size: 12px">
            <template v-if="lastResult.request_id">
              <span class="mono">请求 {{ lastResult.request_id }}</span>
            </template>
            <template v-if="lastResult.execution">
              <span>{{ EXEC_LABEL[lastResult.execution] || lastResult.execution }}</span>
            </template>
            <span v-if="lastResult.raw" class="mono">{{ lastResult.raw }}</span>
          </div>
        </div>
      </div>

      <!-- 右：设备状态与实时事件 -->
      <div class="grid" style="grid-template-rows: auto auto">
        <div class="panel">
          <h2>设备状态</h2>
          <table v-if="store.status">
            <tbody>
              <tr>
                <td>工作模式</td>
                <td>
                  <span class="badge" :class="store.status.mode === 'mock' ? 'gray' : 'blue'">
                    {{ store.status.mode === 'mock' ? '模拟模式（无硬件）' : '真实串口' }}
                  </span>
                </td>
              </tr>
              <tr>
                <td>连接状态</td>
                <td>
                  <span class="dot" :class="store.status.online ? 'online' : 'offline'"></span>
                  {{ store.status.online ? '在线' : '离线' }}
                </td>
              </tr>
              <tr>
                <td>模拟门锁</td>
                <td>
                  <span class="dot" :class="store.status.lock_state === 'LOCKED' ? 'locked' : store.status.lock_state === 'UNLOCKED' ? 'unlocked' : 'fault'"></span>
                  {{ store.status.lock_state === 'UNLOCKED' ? '已解锁（模拟开门中）' : store.status.lock_state === 'LOCKED' ? '已锁定' : store.status.lock_state }}
                </td>
              </tr>
              <tr v-if="store.status.port">
                <td>串口</td>
                <td class="mono">{{ store.status.port }} @ {{ store.status.baud }}</td>
              </tr>
              <tr>
                <td>最后心跳</td>
                <td>{{ store.status.last_heartbeat || '—' }}</td>
              </tr>
              <tr v-if="store.status.last_error">
                <td>串口诊断</td>
                <td style="color: var(--amber); overflow-wrap: anywhere">{{ store.status.last_error }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="muted">加载中…</div>

          <div class="toolbar" style="margin-top: 12px; margin-bottom: 0">
            <template v-if="store.status?.mode !== 'serial'">
              <input v-model="comPort" placeholder="COM9" style="width: 80px" />
              <input v-model.number="baud" type="number" placeholder="115200" style="width: 90px" />
              <button class="btn primary" :disabled="switching" @click="switchToSerial">
                {{ switching ? '切换中…' : '切到真实串口' }}
              </button>
            </template>
            <template v-else>
              <button class="btn" :disabled="switching" @click="switchToMock">
                {{ switching ? '切换中…' : '切回模拟模式' }}
              </button>
            </template>
          </div>
          <div v-if="switchError" class="banner-error" style="margin-top: 8px">{{ switchError }}</div>
        </div>

        <div class="panel">
          <h2>今日统计</h2>
          <div class="stats" style="grid-template-columns: repeat(4, 1fr); margin-bottom: 0">
            <div class="stat" style="padding: 10px">
              <div class="num" style="font-size: 20px">{{ store.summary?.today_events ?? 0 }}</div>
              <div class="label">通行事件</div>
            </div>
            <div class="stat" style="padding: 10px">
              <div class="num" style="font-size: 20px; color: var(--green)">{{ store.summary?.today_allow ?? 0 }}</div>
              <div class="label">已授权</div>
            </div>
            <div class="stat" style="padding: 10px">
              <div class="num" style="font-size: 20px; color: var(--red)">{{ store.summary?.today_deny ?? 0 }}</div>
              <div class="label">已拒绝</div>
            </div>
            <div class="stat" style="padding: 10px">
              <div class="num" style="font-size: 20px; color: var(--amber)">{{ store.summary?.today_unknown ?? 0 }}</div>
              <div class="label">未知人员</div>
            </div>
          </div>
          <div class="muted" style="margin-top: 10px; font-size: 12px">
            未处理告警 {{ store.summary?.unresolved_alerts ?? 0 }} 条 ·
            已注册人员 {{ store.summary?.total_users ?? 0 }} 人 · 样本 {{ store.edge?.input_mode !== 'legacy' ? (store.edge?.users.reduce((sum, u) => sum + u.train_count + u.validation_count, 0) ?? 0) + (store.edge?.unknown_count ?? 0) : store.summary?.total_samples ?? 0 }} 份
          </div>
        </div>
      </div>
    </div>

    <!-- 最近事件 -->
    <div class="panel" style="margin-top: 16px">
      <h2>最近通行记录 <span class="sub">每 2 秒自动刷新</span></h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>人员</th>
              <th>识别</th>
              <th>授权</th>
              <th>原因</th>
              <th>执行</th>
              <th>请求ID</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ev in store.events" :key="ev.id">
              <td class="muted">{{ ev.event_time }}</td>
              <td>{{ ev.user_name || '—' }}</td>
              <td>
                <span class="badge" :class="recBadgeClass(ev.recognition)">
                  {{ RECOG_LABEL[ev.recognition] || ev.recognition }}
                </span>
              </td>
              <td>
                <span class="badge" :class="authBadgeClass(ev.authorization)">
                  {{ AUTH_LABEL[ev.authorization] || ev.authorization }}
                </span>
              </td>
              <td class="muted">{{ ev.auth_reason }}</td>
              <td class="muted">{{ EXEC_LABEL[ev.execution ?? ''] || ev.execution || '—' }}</td>
              <td class="mono muted">{{ ev.request_id || '—' }}</td>
            </tr>
            <tr v-if="store.events.length === 0">
              <td colspan="7" class="muted" style="text-align: center">暂无记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
