<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { refreshStatus, store } from './api'

let timer: number | undefined

onMounted(() => {
  refreshStatus()
  timer = window.setInterval(refreshStatus, 2000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <header class="topbar">
    <div class="logo">智能门禁 · Smart Access</div>
    <nav>
      <router-link to="/">实时监控</router-link>
      <router-link to="/people">人员与权限</router-link>
      <router-link to="/records">通行记录</router-link>
      <router-link to="/alerts">告警中心</router-link>
    </nav>
    <div class="health">
      <span v-if="store.status">
        <span class="dot" :class="store.status.online ? 'online' : 'offline'"></span>
        {{ store.status.online ? '开发板在线' : '开发板离线' }}
        <span class="muted">（{{ store.status.mode === 'mock' ? '模拟模式' : store.status.port }}）</span>
      </span>
      <span v-else class="muted">连接后端中…</span>
    </div>
  </header>
  <router-view />
</template>
