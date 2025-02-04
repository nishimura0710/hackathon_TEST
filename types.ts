import { z } from "zod"

export const botSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  systemPrompt: z.string(),
})

export type Bot = z.infer<typeof botSchema>

export const messageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant"]),
  content: z.string(),
})

export type Message = z.infer<typeof messageSchema>

