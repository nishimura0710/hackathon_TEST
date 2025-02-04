export const BOTS = {
  schedule: {
    id: "schedule",
    name: "日程調整Bot",
    description: "日程調整を手伝います",
    systemPrompt: "あなたは日程調整の専門家です。ユーザーの日程調整を手伝ってください。",
  },
} as const;

export type BotId = keyof typeof BOTS;
