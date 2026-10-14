<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { AccessEvent } from '../types'

const events = ref<AccessEvent[]>([])
const limit = ref(100)
const filterAuth = ref('')
const filterRec = ref('')
const loading = ref(false)

const RECOG_LABEL: Record<string, string> = {
  no_face: '未检测到人脸',
  multiple: '多张人脸',
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

async function load() {
  loading.value = true
  try {
    events.value = await api.events({
      limit: limit.value,
      authorization: filterAuth.value || undefined,
      recognition: filterRec.value || undefined,
    })
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  window.open('/api/access/events/export', '_blank')
}

function recClass(r: string): string {
  if (r === 'recognized') return 'green'
  if (r === 'unknown') return 'amber'
  if (r === 'no_face') return 'gray'
  if (r === 'multiple') return 'amber'
  return 'red'
}

function authClass(a: string): string {
  if (a === 'allow') return 'green'
  if (a === 'deny') return 'red'
  if (a === 'block') return 'amber'
  return 'gray'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="panel">
      <h2>通行记录 <span class="sub">共 {{ events.length }} 条（当前筛选）</span></h2>
      <div class="toolbar">
        <label class="field">
          授权结果
          <select v-model="filterAuth" @change="load">
            <option value="">全部</option>
            <option value="allow">允许</option>
            <option value="deny">拒绝</option>
            <option value="block">拦截</option>
          </select>
        </label>
        <label class="field">
          识别结果
          <select v-model="filterRec" @change="load">
            <option value="">全部</option>
            <option value="recognized">已识别</option>
            <option value="unknown">未知人员</option>
            <option value="no_face">未检测到人脸</option>
            <option value="multiple">多张人脸</option>
          </select>
        </label>
        <label class="field">
          条数
          <select v-model="limit" @change="load">
            <option :value="50">50</option>
            <option :value="100">100</option>
            <option :value="200">200</option>
          </select>
        </label>
        <button class="btn" :disabled="loading" @click="load">刷新</button>
        <button class="btn primary" @click="exportCsv">导出 CSV（Excel 可直接打开）</button>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>时间</th>
              <th>人员</th>
              <th>识别</th>
              <th>距离</th>
              <th>授权</th>
              <th>原因</th>
              <th>执行状态</th>
              <th>请求ID</th>
              <th>板端原始帧</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ev in events" :key="ev.id">
              <td class="mono muted">{{ ev.id }}</td>
              <td class="muted">{{ ev.event_time }}</td>
              <td>
                <template v-if="ev.user_name">{{ ev.user_name }}（{{ ev.user_no }}）</template>
                <span v-else class="muted">—</span>
              </td>
              <td>
                <span class="badge" :class="recClass(ev.recognition)">
                  {{ RECOG_LABEL[ev.recognition] || ev.recognition }}
                </span>
              </td>
              <td class="mono">{{ ev.confidence?.toFixed(1) ?? '—' }}</td>
              <td>
                <span class="badge" :class="authClass(ev.authorization)">
                  {{ AUTH_LABEL[ev.authorization] || ev.authorization }}
                </span>
              </td>
              <td class="muted">{{ ev.auth_reason }}</td>
              <td class="muted">{{ EXEC_LABEL[ev.execution ?? ''] || ev.execution || '—' }}</td>
              <td class="mono muted">{{ ev.request_id || '—' }}</td>
              <td class="mono muted">{{ ev.raw || '—' }}</td>
            </tr>
            <tr v-if="events.length === 0">
              <td colspan="10" class="muted" style="text-align: center">暂无记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
