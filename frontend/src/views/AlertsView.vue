<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { Alert } from '../types'

const alerts = ref<Alert[]>([])
const showResolved = ref(false)
const loading = ref(false)

const TYPE_LABEL: Record<string, string> = {
  unknown_person: '未知人员',
  repeated_failure: '连续失败',
  device_offline: '设备离线',
  device_online: '设备上线',
  serial_busy: '串口/板端错误',
  unconfirmed: '执行未确认',
}

const LEVEL_CLASS: Record<string, string> = {
  critical: 'red',
  warning: 'amber',
  info: 'blue',
}

async function load() {
  loading.value = true
  try {
    alerts.value = await api.alerts({ limit: 200, resolved: showResolved.value || undefined })
  } finally {
    loading.value = false
  }
}

async function resolve(a: Alert) {
  await api.resolveAlert(a.id)
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="panel">
      <h2>告警中心 <span class="sub">未知人员 / 连续失败 / 设备离线 / 串口错误 / 执行未确认</span></h2>
      <div class="toolbar">
        <label class="field">
          显示
          <select v-model="showResolved" @change="load">
            <option :value="false">仅未处理</option>
            <option :value="true">全部（含已处理）</option>
          </select>
        </label>
        <button class="btn" :disabled="loading" @click="load">刷新</button>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>级别</th>
              <th>类型</th>
              <th>人员</th>
              <th>内容</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in alerts" :key="a.id">
              <td class="muted">{{ a.alert_time }}</td>
              <td>
                <span class="badge" :class="LEVEL_CLASS[a.level] || 'gray'">{{ a.level }}</span>
              </td>
              <td>{{ TYPE_LABEL[a.type] || a.type }}</td>
              <td>{{ a.user_name || '—' }}</td>
              <td>{{ a.message }}</td>
              <td>
                <span class="badge" :class="a.resolved ? 'gray' : 'amber'">
                  {{ a.resolved ? '已处理' : '未处理' }}
                </span>
              </td>
              <td>
                <button v-if="!a.resolved" class="btn small" @click="resolve(a)">标记已处理</button>
              </td>
            </tr>
            <tr v-if="alerts.length === 0">
              <td colspan="7" class="muted" style="text-align: center">暂无告警</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
