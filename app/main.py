from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import os
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import json

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
        "system_prompt": "あなたは日程調整の専門家です。ユーザーの日程調整を手伝ってください。",
    },
    "research": {
        "id": "research",
        "name": "リサーチBot",
        "description": "調査研究を支援します",
        "system_prompt": "あなたは研究調査の専門家です。ユーザーの調査を手伝ってください。",
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
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        
        # システムプロンプトとユーザーメッセージを結合
        messages = [
            {"role": "system", "content": BOTS[bot_id]["system_prompt"]},
            *request.messages
        ]
        
        # Claude APIを使用してレスポンスを生成
        response = client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1000,
            messages=messages,
            stream=True
        )
        
        return response
        
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
