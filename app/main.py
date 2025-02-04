from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, AsyncGenerator
import anthropic
import os
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import json
import asyncio

load_dotenv()

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ボット設定
BOTS = {
    "schedule": {
        "id": "schedule",
        "name": "日程調整Bot",
        "description": "日程調整を手伝います",
        "system_prompt": """あなたは日程調整の専門家です。ユーザーの日程調整を手伝ってください。
ただし、現時点ではGoogleカレンダーとの連携は実装されていないため、
カレンダーの情報は取得できません。その旨をユーザーに伝えてください。""",
    }
}

# Google Calendar API設定
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
credentials = None

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]

@app.get("/bots")
async def get_bots():
    return list(BOTS.values())

@app.get("/bots/{bot_id}")
async def get_bot(bot_id: str):
    if bot_id not in BOTS:
        raise HTTPException(status_code=404, detail="Bot not found")
    return BOTS[bot_id]

@app.post("/chat/{bot_id}")
async def chat(bot_id: str, request: ChatRequest):
    if bot_id not in BOTS:
        raise HTTPException(status_code=404, detail="Bot not found")
    
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")
        
        print(f"Processing chat request for bot: {bot_id}")
        print(f"Received messages: {request.messages}")
            
        client = anthropic.AsyncAnthropic(api_key=api_key)
        
        # システムプロンプトとユーザーメッセージを結合
        system_prompt = BOTS[bot_id]["system_prompt"]
        user_message = request.messages[-1].content
        
        print(f"System prompt: {system_prompt}")
        print(f"User message: {user_message}")
        
        # Claude APIを使用してレスポンスを生成
        message = await client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            system=system_prompt
        )
        
        async def generate() -> AsyncGenerator[str, None]:
            content = message.content
            print(f"Full response content: {content}")
            
            # Send role first
            yield "data: " + json.dumps({
                "id": message.id,
                "object": "chat.completion.chunk",
                "created": int(datetime.now().timestamp()),
                "model": "claude-3-opus-20240229",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant"
                    }
                }]
            }) + "\n\n"
            
            # Send content
            yield "data: " + json.dumps({
                "id": message.id,
                "object": "chat.completion.chunk",
                "created": int(datetime.now().timestamp()),
                "model": "claude-3-opus-20240229",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "content": content
                    }
                }]
            }) + "\n\n"
            
            # Send completion
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/google")
async def google_auth():
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        SCOPES
    )
    auth_url = flow.authorization_url()
    return {"auth_url": auth_url[0]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
