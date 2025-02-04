"use client"
import React from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import type { Bot } from "../types"

type ChatInterfaceProps = {
  bot: Bot
}

export const ChatInterface = ({ bot }: ChatInterfaceProps) => {
  const [messages, setMessages] = React.useState<Array<{ role: string; content: string; id: string }>>([])
  const [input, setInput] = React.useState("")
  const [isLoading, setIsLoading] = React.useState(false)

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInput(e.target.value)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim()) return

    const userMessage = {
      id: Date.now().toString(),
      role: "user",
      content: input
    }

    setMessages(prev => [...prev, userMessage])
    setInput("")
    setIsLoading(true)

    try {
      const response = await fetch(`https://backend-app-mkawqchd-1738594929.fly.dev/chat/${bot.id}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
        },
        body: JSON.stringify({
          messages: [...messages, { role: "user", content: input }]
        })
      })

      if (!response.ok) {
        throw new Error("Failed to send message")
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error("No response body")
      }

      const decoder = new TextDecoder()
      let buffer = ""
      let assistantMessage = {
        id: Date.now().toString(),
        role: "assistant",
        content: ""
      }

      setMessages(prev => [...prev, assistantMessage])

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""

        for (const line of lines) {
          if (line.trim() === "") continue
          if (line.trim() === "data: [DONE]") continue
          
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.choices?.[0]?.delta?.content || data.choices?.[0]?.delta?.role) {
                if (data.choices[0].delta.content) {
                  assistantMessage.content += data.choices[0].delta.content
                  setMessages(prev => 
                    prev.map(msg => 
                      msg.id === assistantMessage.id ? assistantMessage : msg
                    )
                  )
                }
              }
            } catch (e) {
              console.error("Failed to parse SSE message:", line, e)
            }
          }
        }
      }
    } catch (error) {
      console.error("Chat error:", error)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle>{bot.name}</CardTitle>
      </CardHeader>
      <CardContent className="h-[60vh] overflow-y-auto">
        <div className="flex flex-col space-y-4">
          {messages.map((m, index) => {
            if (!m.content && m.role === "assistant" && index < messages.length - 1) {
              return null;
            }
            
            let content = m.content;
            if (content && typeof content === 'string') {
              content = content.trim();
            }
            
            return (
              <div key={index} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[80%] ${m.role === "user" ? "bg-blue-500 text-white" : "bg-gray-200 text-black"} p-3 rounded-lg whitespace-pre-wrap break-words`}>
                  {content}
                </div>
              </div>
            );
          })}
          {isLoading && (
            <div className="flex justify-start">
              <div className="max-w-[80%] bg-gray-200 text-black p-3 rounded-lg">
                <span className="animate-pulse">...</span>
              </div>
            </div>
          )}
        </div>
      </CardContent>
      <CardFooter>
        <form onSubmit={handleSubmit} className="flex w-full space-x-2">
          <Input value={input} onChange={handleInputChange} placeholder="メッセージを入力..." className="flex-grow" />
          <Button type="submit" disabled={isLoading}>
            {isLoading ? "送信中..." : "送信"}
          </Button>
        </form>
      </CardFooter>
    </Card>
  )
}

