// 与后端 schemas.py 对应的类型定义

export interface User {
  id: number
  user_no: string
  name: string
  enabled: boolean
  recognition_threshold: number | null
  created_at: string
  sample_count: number
}

export interface Permission {
  id: number
  user_id: number
  device_id: number
  device_no: string
  device_name: string
  user_no: string
  user_name: string
  valid_from: string
  valid_to: string
  enabled: boolean
  created_at: string
}

export interface Device {
  id: number
  device_no: string
  name: string
  com_port: string | null
  baud: number | null
  online: boolean
  lock_state: string
  last_heartbeat: string | null
  created_at: string
}

export interface BridgeStatus {
  mode: 'mock' | 'serial'
  online: boolean
  lock_state: string
  port: string | null
  baud: number | null
  last_heartbeat: string | null
  line_ending: string
  last_error?: string
}

export interface FrameResult {
  ok: boolean
  message: string
  recognition: string
  confidence: number | null
  recognized_no: string | null
  recognized_name: string | null
  authorization: string
  auth_reason: string
  request_id: string | null
  execution: string | null
  raw: string | null
  device_online: boolean
  device_lock_state: string
  event_id: number | null
}

export interface AccessEvent {
  id: number
  event_time: string
  user_id: number | null
  user_no: string | null
  user_name: string | null
  device_no: string | null
  recognition: string
  confidence: number | null
  authorization: string
  auth_reason: string
  request_id: string | null
  execution: string | null
  raw: string | null
}

export interface Alert {
  id: number
  alert_time: string
  type: string
  level: string
  user_id: number | null
  user_name: string | null
  message: string
  resolved: boolean
}

export interface Summary {
  today_events: number
  today_allow: number
  today_deny: number
  today_unknown: number
  unresolved_alerts: number
  total_users: number
  total_samples: number
  device: BridgeStatus
}

export interface FaceStatus {
  model_version: string | null
  total_samples: number
  users: {
    user_id: number
    user_no: string
    name: string
    threshold: number | null
    sample_count: number
  }[]
  default_threshold: number
}

export interface CalibrateDetail {
  user_id: number
  user_no: string
  name: string
  sample_count: number
  threshold: number
  own_max: number | null
  own_mean: number | null
  cross_min: number | null
  note: string
  warning?: string
}

export interface CalibrateResult {
  ok: boolean
  message: string
  details: CalibrateDetail[]
  summary: { users_calibrated: number; cross_risk_users: number; model_version: string | null } | null
}
