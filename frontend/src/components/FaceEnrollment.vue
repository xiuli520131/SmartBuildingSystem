<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, refreshFace, refreshStatus, refreshUsers, store } from '../api'
import type { EnrollmentSource, SampleRole, User } from '../types'

const props = defineProps<{ user: User; boardMode: boolean }>()
const emit = defineEmits<{ close: [] }>()
const source = ref<EnrollmentSource>('upload')
const role = ref<SampleRole>('train')
const busy = ref(false)
const status = ref('')
const failed = ref(false)
const video = ref<HTMLVideoElement>()
const dialog = ref<HTMLElement>()
const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
const cameraOpening = ref(false)
const cameraReady = ref(false)
const cameraError = ref('')
const cameraDevices = ref<MediaDeviceInfo[]>([])
const cameraId = ref('')
let stream: MediaStream | null = null
let cameraGeneration = 0
let disposed = false
type Photo = { file: File; url: string; state: 'pending' | 'saving' | 'saved' | 'error'; message: string }
const photos = ref<Photo[]>([])
const activePhoto = ref(0)
const progress = ref('')
const roleLabel = computed(() => ({ train: '训练模板', validation: '独立验证', unknown: '陌生人验证' }[role.value]))
const personCounts = computed(() => store.edge?.users.find(u => u.user_id === props.user.id))
const sourceCounts = computed(() => (store.edge?.source_counts ?? []).filter(s => s.user_id === props.user.id))
const pending = computed(() => photos.value.filter(p => p.state === 'pending'))
const boardPending = computed(() => !!store.edge?.enrollment)
const saveDisabled = computed(() => busy.value || (source.value === 'board'
  ? !store.edge?.ready || boardPending.value
  : source.value === 'webcam' ? !cameraReady.value : !pending.value.length))

function sourceName(value: string) {
  return ({ board: '板载', upload: '照片', webcam: '电脑' } as Record<string, string>)[value] ?? value
}

function stopCamera() {
  ++cameraGeneration
  stream?.getTracks().forEach(track => track.stop())
  stream = null
  cameraReady.value = false
  cameraOpening.value = false
  if (video.value) video.value.srcObject = null
}

async function startCamera() {
  stopCamera()
  const generation = cameraGeneration
  cameraError.value = ''
  cameraOpening.value = true
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('请使用本机 localhost / 127.0.0.1 地址，或 HTTPS 页面')
    const opened = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 },
        ...(cameraId.value ? { deviceId: { exact: cameraId.value } } : {}) }, audio: false,
    })
    if (disposed || generation !== cameraGeneration || source.value !== 'webcam') {
      opened.getTracks().forEach(track => track.stop())
      return
    }
    stream = opened
    await nextTick()
    if (!video.value) { stopCamera(); return }
    video.value.srcObject = opened
    await video.value.play()
    if (generation !== cameraGeneration || disposed) return
    cameraReady.value = true
    const activeId = opened.getVideoTracks()[0]?.getSettings().deviceId
    if (activeId) cameraId.value = activeId
    try { cameraDevices.value = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput') }
    catch { /* The selected camera remains usable if device enumeration is denied. */ }
    opened.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
      if (generation === cameraGeneration) {
        cameraReady.value = false
        cameraError.value = '摄像头已断开，请重新打开或选择另一台摄像头'
      }
    }))
  } catch (error) {
    if (generation !== cameraGeneration || disposed) return
    stopCamera()
    const name = error instanceof DOMException ? error.name : ''
    cameraError.value = name === 'NotAllowedError' ? '摄像头权限被拒绝，请在浏览器地址栏允许使用摄像头后重试'
      : name === 'NotFoundError' || name === 'OverconstrainedError' ? '未找到所选摄像头，请连接设备或改选其他摄像头'
      : name === 'NotReadableError' ? '摄像头无法读取，请关闭正在占用它的软件后重试'
      : error instanceof Error ? error.message : String(error)
  } finally {
    if (generation === cameraGeneration) cameraOpening.value = false
  }
}

watch(source, () => {
  stopCamera()
  status.value = ''
  failed.value = false
})

function clearPhotos() {
  photos.value.forEach(photo => URL.revokeObjectURL(photo.url))
  photos.value = []
  activePhoto.value = 0
}

function selectPhotos(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  clearPhotos()
  status.value = ''
  failed.value = false
  const errors: string[] = []
  for (const file of files.slice(0, 20)) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      errors.push(`${file.name}：请选择 JPEG、PNG 或 WebP`)
    } else if (file.size > 10 * 1024 * 1024) {
      errors.push(`${file.name}：超过 10 MB，请先缩小照片`)
    } else {
      photos.value.push({ file, url: URL.createObjectURL(file), state: 'pending', message: '等待保存' })
    }
  }
  if (files.length > 20) errors.push('一次最多选择 20 张照片')
  if (errors.length) { failed.value = true; status.value = errors.join('；') }
  input.value = ''
}

async function photoBase64(photo: Photo): Promise<string> {
  const img = new Image()
  img.src = photo.url
  await img.decode()
  if (img.naturalWidth * img.naturalHeight > 40_000_000) throw new Error('照片超过 4000 万像素，请缩小后再上传')
  return frameBase64(img, img.naturalWidth, img.naturalHeight)
}

function frameBase64(frame: CanvasImageSource, width: number, height: number): string {
  const scale = Math.min(1, 1280 / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器无法读取照片，请换用 Edge 或 Chrome')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(frame, 0, 0, canvas.width, canvas.height)
  let encoded = ''
  for (const quality of [0.95, 0.9, 0.8]) {
    encoded = canvas.toDataURL('image/jpeg', quality).split(',')[1]
    if (encoded.length <= 2_000_000) return encoded
  }
  throw new Error('图像编码后仍过大，请降低图片尺寸后重试')
}

async function saveImage(image: string, from: 'upload' | 'webcam', targetRole: SampleRole) {
  if (props.boardMode) {
    return api.edgeImageSample(targetRole === 'unknown' ? null : props.user.id, targetRole, image, from)
  }
  return api.faceSample(props.user.id, image)
}

async function save() {
  if (saveDisabled.value) return
  busy.value = true
  failed.value = false
  status.value = ''
  const targetRole = role.value
  try {
    if (source.value === 'board') {
      const result = await api.edgeEnroll(targetRole === 'unknown' ? null : props.user.id, targetRole)
      status.value = result.message
    } else if (source.value === 'webcam') {
      if (!video.value?.videoWidth) throw new Error('摄像头还没有输出画面，请稍后重试')
      const result = await saveImage(frameBase64(video.value, video.value.videoWidth, video.value.videoHeight), 'webcam', targetRole)
      status.value = result.message
      failed.value = !result.ok
    } else {
      const queue = [...pending.value]
      let saved = 0
      for (let i = 0; i < queue.length; ++i) {
        if (disposed) break
        const photo = queue[i]!
        activePhoto.value = photos.value.indexOf(photo)
        progress.value = `${i + 1} / ${queue.length}`
        photo.state = 'saving'
        photo.message = '正在检测并保存…'
        try {
          const result = await saveImage(await photoBase64(photo), 'upload', targetRole)
          photo.state = result.ok ? 'saved' : 'error'
          photo.message = result.message
          if (result.ok) ++saved
        } catch (error) {
          photo.state = 'error'
          photo.message = error instanceof Error ? error.message : String(error)
        }
      }
      failed.value = saved < queue.length
      status.value = `已保存 ${saved} / ${queue.length} 张。${saved < queue.length ? '未保存原因见照片列表；更换照片后可重新提交。' : '请继续录入独立验证样本。'}`
    }
    if (!disposed) {
      // Saving is already complete even if the following list refresh fails.
      await Promise.allSettled([refreshStatus(), refreshUsers(), refreshFace()])
    }
  } catch (error) {
    failed.value = true
    status.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
    progress.value = ''
  }
}

function onEscape(event: KeyboardEvent) {
  if (event.key === 'Escape' && !busy.value) emit('close')
  if (event.key === 'Tab' && dialog.value) {
    const targets = Array.from(dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'))
      .filter(target => target.offsetParent !== null)
    const first = targets[0], last = targets[targets.length - 1]
    if (event.shiftKey && (document.activeElement === first || !dialog.value.contains(document.activeElement))) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.value.contains(document.activeElement))) {
      event.preventDefault(); first?.focus()
    }
  }
}
window.addEventListener('keydown', onEscape)
onMounted(() => dialog.value?.querySelector<HTMLButtonElement>('button')?.focus())
onBeforeUnmount(() => {
  disposed = true
  stopCamera()
  clearPhotos()
  window.removeEventListener('keydown', onEscape)
  if (previousFocus?.isConnected) previousFocus.focus()
})
watch(() => props.user.id, () => {
  stopCamera()
  clearPhotos()
  status.value = ''
  role.value = 'train'
})
</script>

<template>
  <div class="modal-mask" @click.self="!busy && emit('close')">
    <section ref="dialog" class="modal enrollment-modal" role="dialog" aria-modal="true" aria-labelledby="enrollment-title">
      <header class="enrollment-header">
        <div>
          <span class="enrollment-eyebrow">人员登记 / FACE ENROLLMENT</span>
          <h3 id="enrollment-title">录入人脸样本</h3>
          <p>{{ user.name }} <span class="mono muted">{{ user.user_no }}</span></p>
        </div>
        <button class="btn close-button" aria-label="关闭人脸录入" :disabled="busy" @click="emit('close')">×</button>
      </header>

      <div class="enrollment-sources" role="group" aria-label="选择样本来源">
        <button v-for="option in ([{ id: 'upload', title: '上传照片', note: '选择已有自拍或证件照' },
          { id: 'webcam', title: 'Windows 摄像头', note: '在电脑上拍摄录入' },
          ...(boardMode ? [{ id: 'board', title: '板载摄像头', note: '使用门口设备采集' }] : [])])"
          :key="option.id" type="button" class="source-option" :class="{ active: source === option.id }"
          :aria-pressed="source === option.id" :disabled="busy" @click="source = option.id as EnrollmentSource">
          <strong>{{ option.title }}</strong><span>{{ option.note }}</span>
        </button>
      </div>

      <div v-if="boardMode" class="enrollment-purpose">
        <label class="field">样本用途
          <select v-model="role" :disabled="busy">
            <option value="train">训练模板</option>
            <option value="validation">独立验证</option>
            <option value="unknown">陌生人验证（不绑定当前人员）</option>
          </select>
        </label>
        <p v-if="role === 'unknown'">请由未登记人员入镜；本次样本不会保存到 {{ user.name }} 名下。</p>
        <p v-else-if="role === 'validation'">请另拍新照片或重新站位。实际通行建议用板载摄像头完成独立验证。</p>
        <p v-else>每人至少 3 份训练样本，建议使用不同角度与光照下独立拍摄的正脸。</p>
      </div>

      <div v-if="source === 'upload'" class="photo-enrollment">
        <label class="upload-picker" :class="{ disabled: busy }">
          <input type="file" accept="image/jpeg,image/png,image/webp" multiple :disabled="busy" @change="selectPhotos" />
          <strong>{{ photos.length ? '重新选择照片' : '选择自拍照片' }}</strong>
          <span>JPEG / PNG / WebP · 每张最多 10 MB · 一次最多 20 张</span>
        </label>
        <div v-if="photos.length" class="photo-workspace">
          <figure class="photo-preview"><img :src="photos[activePhoto]?.url" alt="当前选择的人脸录入照片" /></figure>
          <ul class="photo-queue" aria-label="照片保存结果">
            <li v-for="(photo, index) in photos" :key="photo.url">
              <button class="photo-row" :class="{ selected: activePhoto === index }" @click="activePhoto = index">
                <span class="photo-name">{{ photo.file.name }}</span>
                <span class="badge" :class="photo.state === 'saved' ? 'green' : photo.state === 'error' ? 'red' : 'gray'">
                  {{ { pending: '待保存', saving: '处理中', saved: '已保存', error: '未保存' }[photo.state] }}
                </span>
                <small>{{ photo.message }}</small>
              </button>
            </li>
          </ul>
        </div>
        <p v-else class="capture-help">照片中应只有一张无遮挡正脸。请避免美颜、滤镜和过度压缩。</p>
      </div>

      <div v-else-if="source === 'webcam'" class="webcam-enrollment">
        <div class="video-box">
          <video ref="video" autoplay muted playsinline aria-label="Windows 摄像头预览"></video>
          <div v-if="!cameraReady" class="camera-placeholder">
            <strong>{{ cameraOpening ? '正在请求摄像头…' : '开启电脑摄像头' }}</strong>
            <span>{{ cameraError || '浏览器会请求摄像头权限，仅在点击保存时提交一帧。' }}</span>
          </div>
          <span v-if="cameraReady" class="camera-live">摄像头已开启</span>
        </div>
        <div class="toolbar camera-tools">
          <label v-if="cameraDevices.length > 1" class="field">摄像头
            <select v-model="cameraId" :disabled="busy || cameraOpening" @change="startCamera">
              <option v-for="(device, index) in cameraDevices" :key="device.deviceId" :value="device.deviceId">{{ device.label || `摄像头 ${index + 1}` }}</option>
            </select>
          </label>
          <button class="btn" :disabled="busy || cameraOpening" @click="startCamera">{{ cameraReady ? '重新打开' : '打开摄像头' }}</button>
          <button v-if="cameraReady" class="btn" :disabled="busy" @click="stopCamera">关闭摄像头</button>
        </div>
        <p class="capture-help">请正对摄像头，点击“拍摄并保存”。录入多份样本时，适度改变位置。</p>
      </div>

      <div v-else class="board-enrollment">
        <span class="badge" :class="store.edge?.ready ? 'green' : 'amber'">{{ store.edge?.ready ? '板端特征模块就绪' : '板端尚未就绪' }}</span>
        <h4>面对开发板摄像头</h4>
        <p>保持正脸完整入镜。点击采集后，开发板在 30 秒内上传一个合格特征。</p>
        <p v-if="!store.edge?.ready">请检查真实串口连接。开发板离线时，可切换到上传照片或 Windows 摄像头录入。</p>
        <p v-if="boardPending" role="status">正在采集，剩余 {{ store.edge?.enrollment?.remaining_seconds }} 秒。</p>
        <p v-if="store.edge?.last_result?.kind === 'enrollment'">最近板端保存结果：{{ store.edge.last_result.message }}</p>
      </div>

      <p v-if="status" class="capture-feedback" :class="{ error: failed }" role="status">{{ status }}</p>
      <div v-if="boardMode" class="enrollment-counts">
        <span>当前人员：<strong>{{ personCounts?.train_count ?? 0 }}</strong> 训练 / <strong>{{ personCounts?.validation_count ?? 0 }}</strong> 验证</span>
        <span v-if="sourceCounts.length" class="muted">{{ ['board', 'upload', 'webcam'].map(s => `${sourceName(s)} ${sourceCounts.filter(c => c.source === s).reduce((sum, c) => sum + c.count, 0)}`).join(' · ') }}</span>
      </div>
      <footer class="enrollment-footer">
        <p>{{ boardMode ? '新增样本会使校准失效；采集完成后请重新校准。' : '照片按旧模式保存；样本足够后可训练和校准。' }}</p>
        <button class="btn primary" :disabled="saveDisabled" @click="save">
          {{ busy ? `处理中 ${progress}` : source === 'upload' ? `保存${pending.length ? ` ${pending.length} 张` : ''}${boardMode ? roleLabel : '样本'}` : source === 'webcam' ? '拍摄并保存' : '采集板载特征' }}
        </button>
        <button class="btn" :disabled="busy" @click="emit('close')">完成</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.enrollment-modal { width: min(850px, 94vw); }
.enrollment-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.enrollment-eyebrow { display: block; color: var(--text-dim); font-size: 11px; letter-spacing: 1.4px; margin-bottom: 7px; }
.enrollment-header h3 { font-size: 23px; margin-bottom: 6px; }
.enrollment-header p { display: flex; gap: 12px; align-items: center; }
.close-button { min-width: 44px; min-height: 44px; font-size: 24px; padding: 0; }
.enrollment-sources { display: flex; gap: 10px; margin-bottom: 18px; }
.source-option { flex: 1; text-align: left; padding: 13px 14px; border: 1px solid var(--border); border-radius: 9px; background: var(--panel-2); color: var(--text); cursor: pointer; }
.source-option strong, .source-option span { display: block; }
.source-option span { color: var(--text-dim); font-size: 12px; margin-top: 5px; line-height: 1.5; }
.source-option.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.source-option:focus-visible, .upload-picker:focus-within, .photo-row:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.source-option:disabled { cursor: wait; opacity: .6; }
.enrollment-purpose { display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 16px; align-items: center; margin-bottom: 18px; }
.enrollment-purpose p, .capture-help { color: var(--text-dim); font-size: 13px; line-height: 1.7; }
.upload-picker { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 8px; padding: 24px 14px; background: var(--panel-2); border: 1px dashed var(--text-dim); border-radius: 9px; cursor: pointer; position: relative; }
.upload-picker input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.upload-picker span { color: var(--text-dim); font-size: 12px; text-align: center; }
.upload-picker.disabled { opacity: .5; cursor: wait; }
.capture-help { margin-top: 12px; }
.photo-workspace { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }
.photo-preview { background: #080c13; border-radius: 8px; height: 265px; overflow: hidden; }
.photo-preview img { width: 100%; height: 100%; object-fit: contain; }
.photo-queue { list-style: none; max-height: 265px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.photo-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 8px; width: 100%; text-align: left; padding: 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--panel-2); color: var(--text); cursor: pointer; }
.photo-row.selected { border-color: var(--accent); }
.photo-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.photo-row small { grid-column: 1 / -1; font-size: 12px; line-height: 1.5; color: var(--text-dim); overflow-wrap: anywhere; }
.webcam-enrollment .video-box { max-height: 330px; }
.camera-placeholder { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 12px; padding: 25px; text-align: center; }
.camera-placeholder span { max-width: 420px; color: var(--text-dim); line-height: 1.7; }
.camera-live { position: absolute; left: 12px; bottom: 12px; border-radius: 4px; padding: 5px 10px; background: var(--panel); color: var(--green); font-size: 12px; }
.camera-tools { margin: 12px 0 0; flex-wrap: wrap; align-items: flex-end; }
.board-enrollment { background: var(--panel-2); border: 1px solid var(--border); padding: 20px; border-radius: 9px; min-height: 175px; }
.board-enrollment h4 { margin: 14px 0 8px; font-size: 17px; }
.board-enrollment p { color: var(--text-dim); line-height: 1.8; }
.capture-feedback { margin-top: 14px; padding: 12px; line-height: 1.7; background: var(--panel-2); border-left: 3px solid var(--green); overflow-wrap: anywhere; }
.capture-feedback.error { border-color: var(--amber); }
.enrollment-counts { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 16px; font-size: 13px; }
.enrollment-footer { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.enrollment-footer p { flex-basis: 100%; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; }
.enrollment-footer button { min-height: 44px; }
@media (max-width: 600px) {
  .enrollment-sources { flex-direction: column; }
  .source-option { padding: 10px 12px; }
  .source-option span { margin-top: 2px; }
  .enrollment-purpose, .photo-workspace { grid-template-columns: 1fr; }
  .photo-preview { height: 200px; }
  .photo-queue { max-height: 180px; }
}
</style>
