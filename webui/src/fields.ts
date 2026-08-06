/**
 * 模板字段定义，与后端 core/chat_profile.py 的 TEMPLATE_GROUPS 对齐。
 * hint 文案与官方 _conf_schema.json 对齐。
 * type: text | textarea | number | bool
 */

export interface FieldDef {
  key: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'bool'
  step?: number
  placeholder?: string
  hint?: string
}

export interface GroupDef {
  key: string
  label: string
  desc: string
  fields: FieldDef[]
}

export const GROUPS: GroupDef[] = [
  {
    key: 'personality',
    label: '助理画像',
    desc: 'AI 的身份定位与回复策略指引',
    fields: [
      { key: 'ai_self_identity', label: '自我身份设定', type: 'textarea', placeholder: 'AI 的角色、性格与知识定位', hint: '描述助理的身份、能力、兴趣，前台根据这些信息判断什么消息跟助理相关。' },
      { key: 'reply_strategy_guide', label: '回复策略指引', type: 'textarea', placeholder: '告诉 AI 在群里应该怎么说话、何时说话', hint: '告诉前台「助理关心什么话题、什么情况该接话、什么时候别打扰」。' },
    ],
  },
  {
    key: 'wake_interaction',
    label: '点名与交互',
    desc: '被点名、被召唤时的行为规则',
    fields: [
      { key: 'alias', label: '唤醒词', type: 'text', placeholder: '如：AngelHeart', hint: '用于点名匹配。多个昵称请用 \'|\' 分隔。' },
      { key: 'enter_on_mention_only', label: '仅点名入场', type: 'bool', hint: '启用后，离场时只有点名消息（@或昵称）能入场；关闭后任何消息都会入场处理。' },
      { key: 'force_reply_when_summoned', label: '被点名强制回复', type: 'bool', hint: '启用后，只要消息命中人格名称或助理昵称，助理都会直接回复。' },
      { key: 'reply_even_not_questioned', label: '非提问也回复', type: 'bool', hint: '关闭时，只有群友有求于助理或话题让助理感兴趣才会回复。开启时，即使只是打招呼或闲聊也会回应。' },
      { key: 'block_unapproved_wake_non_command', label: '拦截未认可的唤醒词', type: 'bool', hint: '启用后，命中上游点名但未通过天使之心批准的非命令消息将被直接拦截。' },
      { key: 'slap_words', label: '掌嘴词', type: 'text', placeholder: '听到后进入沉默（竖线分隔）', hint: '当消息中包含这些词时，插件将进入静默状态。多个词请用 \'|\' 分隔。' },
      { key: 'speak_words', label: '张嘴词', type: 'text', placeholder: '听到后解除沉默（竖线分隔）', hint: '当消息中包含这些词时，插件将解除闭嘴状态。多个词请用 \'|\' 分隔。' },
      { key: 'silence_duration', label: '沉默时长（秒）', type: 'number', step: 10, hint: '触发掌嘴后，插件保持静默的时间。' },
    ],
  },
  {
    key: 'leave_reply',
    label: '离场应答',
    desc: '离场状态下对复读、热聊的一次性应答',
    fields: [
      { key: 'leave_echo_reply', label: '复读时离场应答', type: 'bool', hint: '开启后，短时间内同样的文字重复达到设定次数时，我会回复一次。' },
      { key: 'leave_dense_reply', label: '热聊时离场应答', type: 'bool', hint: '开启后，短时间内多人连续发言达到设定条件时，我会回复一次。' },
      { key: 'echo_detection_threshold', label: '复读判定次数', type: 'number', step: 1, hint: '同样的文字在设定时间内达到这个次数，才会回复一次。' },
      { key: 'echo_detection_window', label: '复读判定窗口（秒）', type: 'number', step: 5, hint: '只统计这段时间内重复出现的同样文字。' },
      { key: 'dense_conversation_threshold', label: '热聊判定条数', type: 'number', step: 1, hint: '在设定时间内达到这个消息数，才会判定聊天很热闹。' },
      { key: 'dense_conversation_window', label: '热聊判定窗口（秒）', type: 'number', step: 10, hint: '只统计这段时间内发出的消息。' },
      { key: 'min_participant_count', label: '最少参与人数', type: 'number', step: 1, hint: '参与人数达到这个数量，才会判定多人正在热聊。' },
      { key: 'familiarity_cooldown_duration', label: '回复冷却时长（秒）', type: 'number', step: 60, hint: '因复读或热聊回复一次后，这段时间内不会再触发下一次离场应答。' },
    ],
  },
  {
    key: 'reply_length',
    label: '回复长度',
    desc: '常规回复与焦点回复的字数上限',
    fields: [
      { key: 'focus_instructions', label: '焦点触发词', type: 'text', placeholder: '逗号分隔，命中则按焦点字数回复', hint: '本批群聊消息命中任一短语后，本次回复改用焦点字数提醒。默认值：分析 总结 好好想想 为什么 到底。' },
      { key: 'normal_reply_max_chars', label: '常规回复上限（字）', type: 'number', step: 1, hint: '未命中焦点指令时注入的回复上限。默认值：20。' },
      { key: 'focus_reply_max_chars', label: '焦点回复上限（字）', type: 'number', step: 10, hint: '命中焦点指令时注入的回复上限，不能小于常规回复字数。默认值：200。' },
    ],
  },
  {
    key: 'energy',
    label: '能量设置',
    desc: '回复的精力消耗与恢复',
    fields: [
      { key: 'initial_energy', label: '初始能量', type: 'number', step: 5, hint: '每个群首次创建能量状态时使用的能量值。默认值：100。' },
      { key: 'max_energy', label: '能量上限', type: 'number', step: 5, hint: '能量恢复时不会超过这个值。默认值：100。' },
      { key: 'min_energy', label: '能量下限', type: 'number', step: 5, hint: '回复扣能时不会低于这个值。默认值：-100。' },
      { key: 'recovery_per_second', label: '每秒恢复', type: 'number', step: 0.1, hint: '普通巡检结束前按经过的时间恢复一次能量。默认值：每秒恢复 0.6。' },
      { key: 'base_reply_cost', label: '基础回复消耗', type: 'number', step: 0.5, hint: '每次实际形成回复内容时固定扣除的能量。默认值：14。' },
      { key: 'reply_cost_per_character', label: '每字额外消耗', type: 'number', step: 0.01, hint: '按最终消息链中的有效字符数额外扣除能量。默认值：每字符 0.12。' },
    ],
  },
  {
    key: 'timing',
    label: '回复节奏',
    desc: '防抖时长与在场保持时间',
    fields: [
      { key: 'waiting_time', label: '助理休息时长（秒）', type: 'number', step: 1, hint: '助理被调用后，普通群聊消息不会启动巡检；被点名时可以触发一次加速检查。默认值：30。' },
      { key: 'assistant_debounce_time', label: '点名等待时间（秒）', type: 'number', step: 0.5, hint: '有人 @ 助理或叫到助理时，前台会先整理这段时间内的补充消息，再交给秘书根据最后一条消息判断。默认值：1。' },
      { key: 'secretary_debounce_time', label: '秘书巡检周期（秒）', type: 'number', step: 1, hint: '助理已经参与群聊时，前台启动巡检后最多等待这段时间就会检查助理忙闲、休息和能量，再决定是否交给秘书判断。默认值：30。' },
      { key: 'accelerate_debounce_time', label: '点名加速时间（秒）', type: 'number', step: 0.5, hint: '前台正在等待消息补充时又有人叫到助理，会把本轮等待缩短到这里设定的时长，再交给秘书判断。默认值：1。' },
      { key: 'observation_timeout', label: '在场保持（秒）', type: 'number', step: 10, hint: '助理被叫到后，最多保持参与群聊这么久；超过后，收到下一条消息时会回到不主动参与的状态。默认值：60。' },
    ],
  },
]

export interface TemplateConfig {
  [groupKey: string]: Record<string, unknown>
}

export interface Template {
  id: string
  name: string
  description: string
  created_at: number
  updated_at: number
}

export interface TemplateDetail extends Template {
  config: TemplateConfig
}

export interface ChatItem {
  chat_id: string
  template_id: string | null
  display_name?: string
  kind?: string
}

export interface ChatStatusItem {
  chat_id: string
  display_name: string
  kind: string
  template_id: string | null
  status: {
    current_status: string
    duration_seconds: number
    duration_minutes: number
    has_assistant_debounce: boolean
    has_secretary_debounce: boolean
  }
  energy: number | null
  patrol: {
    waiting: 'secretary' | 'assistant' | 'rest' | ''
    remaining: number
    total: number
  }
  last_decision: {
    chat_id: string
    decided_at: number
    should_reply: boolean
    summary: string
  } | null
}
