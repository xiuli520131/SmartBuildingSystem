<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, formatDT, refreshFace, refreshUsers, refreshStatus, store } from '../api'
import FaceEnrollment from '../components/FaceEnrollment.vue'
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
const boardMode = () => store.edge?.input_mode !== 'legacy'

async function openCapture(u: User) {
  captureUser.value = u
  await refreshStatus()
}

function closeCapture() {
  captureUser.value = null
}

async function trainNow() {
  if (boardMode()) {
    notify('板端模式保存特征模板，无需训练旧照片模型；录入训练和验证样本后进行阈值校准')
    return
  }
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
  if (boardMode()) {
    try {
      const r = await api.edgeCalibrate()
      notify(r.details.map(d => `${d.name}：${d.message}`).join('；') || r.message)
      await refreshStatus()
    } catch (e) { notify(e instanceof Error ? e.message : String(e)) }
    return
  }
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
        <span class="sub">{{ boardMode() ? '板端模式：每人至少 3 个训练特征与 3 个独立验证特征' : '旧照片模型：注册后采集 ≥2 张样本' }}</span>
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
              <td>{{ boardMode() ? `${store.edge?.users.find(e => e.user_id === u.id)?.train_count ?? 0} 训练 / ${store.edge?.users.find(e => e.user_id === u.id)?.validation_count ?? 0} 验证` : u.sample_count }}</td>
              <td class="mono">{{ boardMode() ? (store.edge?.users.find(e => e.user_id === u.id)?.threshold?.toFixed(1) ?? '未校准') : (u.recognition_threshold?.toFixed(1) ?? '—') }}</td>
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
        <button v-if="!boardMode()" class="btn" @click="trainNow">训练模型</button>
        <button class="btn" @click="calibrateNow">阈值校准</button>
        <span class="muted" v-if="store.face && !boardMode()">
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
    <FaceEnrollment v-if="captureUser" :key="captureUser.id" :user="captureUser" :board-mode="boardMode()" @close="closeCapture" />

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
