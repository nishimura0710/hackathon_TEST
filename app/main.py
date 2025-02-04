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
from typing import Optional, Dict

load_dotenv()

app = FastAPI()

# Global variable to store credentials (POC only - would use secure storage in production)
calendar_credentials: Dict[str, Credentials] = {}

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
Googleカレンダーから予定を取得し、空き時間を見つけることができます。
ユーザーの要望に応じて、適切な日時を提案してください。""",
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

@app.get("/calendar/events")
async def get_events(user_ids: str):
    user_id_list = user_ids.split(',')
    now = datetime.now()
    week_later = now + timedelta(days=7)
    
    all_events = {}
    for user_id in user_id_list:
        try:
            events = await get_calendar_events(user_id.strip(), now, week_later)
            all_events[user_id] = events
        except Exception as e:
            all_events[user_id] = {"error": str(e)}
    
    return all_events

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
        
        # カレンダー情報を取得
        user_id = "default_user"
        if bot_id == "schedule" and user_id in calendar_credentials:
            now = datetime.now()
            week_later = now + timedelta(days=7)
            try:
                events = await get_calendar_events(user_id, now, week_later)
                calendar_info = "\n\n利用可能なカレンダー情報:\n"
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    end = event['end'].get('dateTime', event['end'].get('date'))
                    calendar_info += f"- {event['summary']}: {start} から {end}\n"
            except Exception as e:
                calendar_info = "\n\nカレンダー情報の取得に失敗しました。"
        else:
            calendar_info = "\n\nカレンダーの認証が必要です。/auth/google エンドポイントで認証を行ってください。"
            
        client = anthropic.AsyncAnthropic(api_key=api_key)
        
        # システムプロンプトとユーザーメッセージを結合
        system_prompt = BOTS[bot_id]["system_prompt"]
        user_message = request.messages[-1].content + calendar_info
        
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
                "redirect_uris": ["http://localhost:8000/auth/google/callback"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        SCOPES
    )
    auth_url = flow.authorization_url()
    return {"auth_url": auth_url[0]}

@app.post("/auth/google/callback")
async def google_auth_callback(code: str):
    try:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                    "redirect_uris": ["http://localhost:8000/auth/google/callback"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }
            },
            SCOPES
        )
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Store credentials in memory (POC only)
        user_id = "default_user"  # In production, this would be a real user ID
        calendar_credentials[user_id] = credentials
        
        return {"message": "認証が完了しました。カレンダー情報にアクセスできるようになりました。"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

async def get_calendar_events(user_id: str, time_min: datetime, time_max: datetime):
    if user_id not in calendar_credentials:
        raise HTTPException(status_code=401, detail="カレンダーの認証が必要です")
    
    try:
        service = build('calendar', 'v3', credentials=calendar_credentials[user_id])
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        formatted_events = []
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            formatted_events.append({
                'summary': event.get('summary', '予定あり'),
                'start': start,
                'end': end,
                'id': event['id']
            })
            
        return formatted_events

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
