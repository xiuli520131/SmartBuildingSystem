<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { api, cameraFrameToBase64, formatDT, refreshFace, refreshUsers, store } from '../api'
import type { CalibrateResult, Permission, User } from '../types'

// ---------- 用户列表 ----------
const newUser = reactive({ user_no: '', name: '' })
const toast = ref('')
let toastTimer: number | undefined

function notify(msg: string) {
  toast.value = msg
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 3000)
}

async function addUser() {
  if (!newUser.user_no.trim() || !newUser.name.trim()) {
    notify('请填写人员编号和姓名')
    return
  }
  try {
    await api.createUser({
      user_no: newUser.user_no.trim(),
      name: newUser.name.trim(),
    })
    newUser.user_no = ''
    newUser.name = ''
    await refreshUsers()
    notify('人员已创建')
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

async function toggleUser(u: User) {
  try {
    await api.updateUser(u.id, { enabled: !u.enabled })
    await refreshUsers()
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

async function removeUser(u: User) {
  if (!window.confirm(`删除人员「${u.name}」及其全部人脸样本、权限？`)) return
  try {
    await api.deleteUser(u.id)
    await refreshUsers()
    await refreshFace()
    notify('人员已删除')
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

// ---------- 人脸采集 ----------
const captureUser = ref<User | null>(null)
const captureVideo = ref<HTMLVideoElement>()
let captureStream: MediaStream | null = null
const collecting = ref(false)
const shotResult = ref('')
const shotOk = ref(false)
const lastShot = ref<{ count?: number } | null>(null)

async function openCapture(u: User) {
  captureUser.value = u
  shotResult.value = ''
  lastShot.value = null
  try {
    captureStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    })
    await new Promise((r) => setTimeout(r, 50))
    if (captureVideo.value) {
      captureVideo.value.srcObject = captureStream
      await captureVideo.value.play()
    }
  } catch (e) {
    shotResult.value = '无法打开摄像头：' + (e instanceof Error ? e.message : String(e))
    shotOk.value = false
  }
}

async function takeShot() {
  if (!captureVideo.value || !captureVideo.value.videoWidth || !captureUser.value) return
  if (collecting.value) return
  collecting.value = true
  shotResult.value = '提交中…'
  shotOk.value = false
  try {
    const b64 = cameraFrameToBase64(captureVideo.value)
    const r = await api.faceSample(captureUser.value.id, b64)
    shotOk.value = r.ok
    shotResult.value = r.message
    lastShot.value = { count: r.sample_count }
    if (r.ok) await refreshFace()
  } catch (e) {
    shotOk.value = false
    shotResult.value = e instanceof Error ? e.message : String(e)
  } finally {
    collecting.value = false
  }
}

function closeCapture() {
  if (captureStream) {
    captureStream.getTracks().forEach((t) => t.stop())
    captureStream = null
  }
  captureUser.value = null
}

async function trainNow() {
  try {
    const r = await api.faceTrain()
    notify(r.message)
    await refreshFace()
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

const calibrateResult = ref<CalibrateResult | null>(null)

async function calibrateNow() {
  try {
    calibrateResult.value = await api.faceCalibrate()
    await refreshFace()
    await refreshUsers()
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

// ---------- 权限配置 ----------
const permUser = ref<User | null>(null)
const perms = ref<Permission[]>([])
const newPerm = reactive({
  device_id: 1,
  valid_from: '',
  valid_to: '',
})

async function openPerms(u: User) {
  permUser.value = u
  perms.value = await api.permissions(u.id)
  const now = new Date()
  const fmt = (d: Date) => {
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
  }
  newPerm.valid_from = fmt(now)
  const end = new Date(now.getTime() + 365 * 24 * 3600 * 1000)
  newPerm.valid_to = fmt(end)
}

async function addPerm() {
  if (!permUser.value) return
  try {
    await api.createPermission({
      user_id: permUser.value.id,
      device_id: newPerm.device_id,
      valid_from: formatDT(newPerm.valid_from),
      valid_to: formatDT(newPerm.valid_to),
    })
    perms.value = await api.permissions(permUser.value.id)
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

async function togglePerm(p: Permission) {
  try {
    await api.updatePermission(p.id, { enabled: !p.enabled })
    if (permUser.value) perms.value = await api.permissions(permUser.value.id)
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

async function removePerm(p: Permission) {
  try {
    await api.deletePermission(p.id)
    if (permUser.value) perms.value = await api.permissions(permUser.value.id)
  } catch (e) {
    notify(e instanceof Error ? e.message : String(e))
  }
}

onMounted(() => {
  refreshUsers()
  refreshFace()
  api.devices().then((d) => (store.devices = d)).catch(() => {})
})

onUnmounted(() => {
  closeCapture()
})
</script>

<template>
  <div class="page">
    <!-- 添加人员 -->
    <div class="panel" style="margin-bottom: 16px">
      <h2>添加人员</h2>
      <div class="form-row">
        <label class="field">
          人员编号
          <input v-model="newUser.user_no" placeholder="如 U003" />
        </label>
        <label class="field">
          姓名
          <input v-model="newUser.name" placeholder="如 张三" />
        </label>
        <button class="btn primary" style="margin-top: 14px" @click="addUser">创建</button>
      </div>
    </div>

    <!-- 人员列表 -->
    <div class="panel">
      <h2>
        人员列表
        <span class="sub">注册后采集 ≥2 张人脸样本即可训练识别模型</span>
      </h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>姓名</th>
              <th>状态</th>
              <th>样本数</th>
              <th>识别阈值</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in store.users" :key="u.id">
              <td class="mono">{{ u.user_no }}</td>
              <td>{{ u.name }}</td>
              <td>
                <span class="badge" :class="u.enabled ? 'green' : 'gray'">
                  {{ u.enabled ? '启用' : '停用' }}
                </span>
              </td>
              <td>{{ u.sample_count }}</td>
              <td class="mono">{{ u.recognition_threshold?.toFixed(1) ?? '—' }}</td>
              <td>
                <button class="btn small" @click="openCapture(u)">采集人脸</button>
                <button class="btn small" @click="openPerms(u)">权限</button>
                <button class="btn small" @click="toggleUser(u)">
                  {{ u.enabled ? '停用' : '启用' }}
                </button>
                <button class="btn small danger" @click="removeUser(u)">删除</button>
              </td>
            </tr>
            <tr v-if="store.users.length === 0">
              <td colspan="6" class="muted" style="text-align: center">暂无人员，请先添加</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="toolbar" style="margin-top: 14px; margin-bottom: 0">
        <button class="btn" @click="trainNow">训练模型</button>
        <button class="btn" @click="calibrateNow">阈值校准</button>
        <span class="muted" v-if="store.face">
          模型版本 {{ store.face.model_version || '未训练' }} · 样本 {{ store.face.total_samples }} 张 ·
          默认阈值 {{ store.face.default_threshold }}
        </span>
      </div>

      <!-- 校准结果 -->
      <div v-if="calibrateResult" style="margin-top: 14px">
        <div class="badge" :class="calibrateResult.ok ? 'green' : 'red'">
          {{ calibrateResult.message }}
        </div>
        <div class="table-wrap" style="margin-top: 10px">
          <table>
            <thead>
              <tr>
                <th>人员</th>
                <th>样本数</th>
                <th>自距离最大</th>
                <th>自距离均值</th>
                <th>与他人员最近</th>
                <th>校准阈值</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in calibrateResult.details" :key="d.user_id">
                <td>{{ d.name }}（{{ d.user_no }}）</td>
                <td>{{ d.sample_count }}</td>
                <td class="mono">{{ d.own_max?.toFixed(1) ?? '—' }}</td>
                <td class="mono">{{ d.own_mean?.toFixed(1) ?? '—' }}</td>
                <td class="mono">{{ d.cross_min?.toFixed(1) ?? '—' }}</td>
                <td class="mono">{{ d.threshold.toFixed(1) }}</td>
                <td class="muted" style="font-size: 12px">
                  {{ d.warning || d.note }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 人脸采集弹窗 -->
    <div v-if="captureUser" class="modal-mask" @click.self="closeCapture">
      <div class="modal">
        <span class="close" @click="closeCapture">×</span>
        <h3>采集人脸样本 — {{ captureUser.name }}（{{ captureUser.user_no }}）</h3>
        <div class="video-box" style="aspect-ratio: 4 / 3">
          <video v-show="!shotResult || shotOk" ref="captureVideo" muted playsinline></video>
          <div v-if="shotResult && !shotOk" class="placeholder" style="padding: 16px; text-align: center">
            {{ shotResult }}
          </div>
        </div>
        <div class="toolbar" style="margin-top: 12px; margin-bottom: 0">
          <button class="btn primary" @click="takeShot" :disabled="collecting">
            采集当前帧
          </button>
          <span v-if="lastShot" class="muted">该人员现有 {{ lastShot.count }} 张样本</span>
        </div>
        <div class="muted" style="margin-top: 10px; font-size: 12px; line-height: 1.7">
          建议采集 5–10 张，轻微转动头部或改变光照。每帧必须<b>恰好检测到一张人脸</b>才会保存；
          采集 ≥2 张后模型自动增量训练。采集完成后点击下方「训练 / 阈值校准」。
        </div>
        <div class="toolbar" style="margin-top: 12px; margin-bottom: 0">
          <button class="btn" @click="trainNow">训练模型</button>
          <button class="btn" @click="calibrateNow">阈值校准</button>
          <button class="btn primary" @click="closeCapture">完成</button>
        </div>
      </div>
    </div>

    <!-- 权限配置弹窗 -->
    <div v-if="permUser" class="modal-mask" @click.self="permUser = null">
      <div class="modal">
        <span class="close" @click="permUser = null">×</span>
        <h3>通行权限 — {{ permUser.name }}（{{ permUser.user_no }}）</h3>

        <div class="form-row">
          <label class="field">
            门设备
            <select v-model="newPerm.device_id">
              <option v-for="d in store.devices" :key="d.id" :value="d.id">
                {{ d.name }}（{{ d.device_no }}）
              </option>
            </select>
          </label>
          <label class="field">
            生效时间
            <input type="datetime-local" v-model="newPerm.valid_from" />
          </label>
          <label class="field">
            失效时间
            <input type="datetime-local" v-model="newPerm.valid_to" />
          </label>
          <button class="btn primary" style="margin-top: 14px" @click="addPerm">添加权限</button>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>设备</th>
                <th>生效</th>
                <th>失效</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in perms" :key="p.id">
                <td>{{ p.device_name }}</td>
                <td class="muted">{{ p.valid_from }}</td>
                <td class="muted">{{ p.valid_to }}</td>
                <td>
                  <span class="badge" :class="p.enabled ? 'green' : 'gray'">
                    {{ p.enabled ? '启用' : '停用' }}
                  </span>
                </td>
                <td>
                  <button class="btn small" @click="togglePerm(p)">
                    {{ p.enabled ? '停用' : '启用' }}
                  </button>
                  <button class="btn small danger" @click="removePerm(p)">删除</button>
                </td>
              </tr>
              <tr v-if="perms.length === 0">
                <td colspan="5" class="muted" style="text-align: center">
                  暂无权限。没有有效权限时，识别成功也会被拒绝。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>
