/**
 * 卡片内的二级分组，纯展示层。
 *
 * 只为字段多的卡片服务：把一个分组按用途切成几块，块内字段顺序仍按 schema 走。
 * 这里只声明「哪些字段归到哪一块」，不参与数据读写，也不改 schema 的路径结构——
 * 配置值仍然是扁平的 leave_reply.xxx，和后端、和原生配置页完全一致。
 *
 * 未在表里出现的分组保持原样平铺，不加子块。
 */

export interface SectionDef {
  /** 子块标题 */
  label: string
  /** 归入本子块的字段键（分组内的键名，不含分组前缀） */
  keys: string[]
}

export const GROUP_SECTIONS: Record<string, SectionDef[]> = {
  leave_reply: [
    {
      label: '应答开关',
      keys: ['leave_echo_reply', 'leave_dense_reply', 'leave_lone_reply'],
    },
    {
      label: '复读判定',
      keys: ['echo_detection_threshold', 'echo_detection_window'],
    },
    {
      label: '热聊判定',
      keys: [
        'dense_conversation_threshold',
        'dense_conversation_window',
        'min_participant_count',
      ],
    },
    {
      label: '触发间隔',
      keys: ['familiarity_cooldown_duration'],
    },
  ],
  wake_interaction: [
    {
      label: '点名与入场',
      keys: [
        'alias',
        'alias_wake_cooldown_seconds',
        'enter_on_mention_only',
        'force_reply_when_summoned',
        'reply_even_not_questioned',
        'block_unapproved_wake_non_command',
      ],
    },
    {
      label: '沉默控制',
      keys: ['slap_words', 'speak_words', 'silence_duration'],
    },
    {
      label: '过滤与戳一戳',
      keys: ['ignored_sender_ids', 'poke_on_reply_enabled', 'poke_on_reply_cooldown'],
    },
  ],
  energy: [
    {
      label: '能量区间',
      keys: ['initial_energy', 'max_energy', 'min_energy'],
    },
    {
      label: '消耗与恢复',
      keys: [
        'recovery_per_second',
        'base_reply_cost',
        'reply_cost_per_character',
      ],
    },
  ],
}
