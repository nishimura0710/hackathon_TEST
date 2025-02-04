from fastapi import FastAPI, HTTPException, Depends, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, AsyncGenerator
import anthropic
import os
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import json
import asyncio
from typing import Optional, Dict, Any
from google.oauth2.credentials import Credentials as GoogleCredentials

load_dotenv()

app = FastAPI()

# Global variable to store credentials (POC only - would use secure storage in production)
calendar_credentials: Dict[str, GoogleCredentials] = {}

# CORS設定
FRONTEND_URLS = [
    "https://hi-chat-app-tunnel-o41v70ny.devinapps.com",
    "https://hi-chat-app-221t4l92.devinapps.com",
    "http://localhost:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS", "HEAD"],
    allow_headers=["Accept", "Content-Type", "Origin", "Authorization", "X-Requested-With"],
    expose_headers=["Content-Type", "Authorization", "Cross-Origin-Opener-Policy"],
    max_age=3600
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response

# ボット設定
BOTS = {
    "schedule": {
        "id": "schedule",
        "name": "日程調整Bot",
        "description": "日程調整を手伝います",
        "system_prompt": """あなたは日程調整の専門家です。Googleカレンダーと連携して、複数の参加者の予定を確認し、最適な会議時間を提案することができます。

以下のような機能があります：
1. 複数の参加者のGoogleカレンダーから予定を取得して確認
2. 全員が参加可能な空き時間を自動的に検出
3. 優先度や時間帯の希望に応じて最適な時間を提案

例えば：
- 「AさんとBさんと来週打ち合わせをしたいです」
- 「3人で今週中に1時間の会議を設定したい」
- 「午前中で都合の良い時間を探して」

このような要望に対して、カレンダー情報を確認して具体的な日時を提案します。
提供されたカレンダー情報に基づいて、全員が参加可能な時間帯を見つけ出し、最適な日時を提案します。""",
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

def find_common_free_slots(events_by_user: dict, start_time: datetime, end_time: datetime) -> list:
    all_busy_slots = []
    
    # Convert all events to busy slots
    for user_events in events_by_user.values():
        if isinstance(user_events, dict) and "error" in user_events:
            continue
            
        for event in user_events:
            start = datetime.fromisoformat(event['start'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(event['end'].replace('Z', '+00:00'))
            all_busy_slots.append((start, end))
    
    # Sort busy slots by start time
    all_busy_slots.sort(key=lambda x: x[0])
    
    # Merge overlapping slots
    if not all_busy_slots:
        return [{"start": start_time.isoformat(), "end": end_time.isoformat()}]
        
    merged_busy = []
    current_start, current_end = all_busy_slots[0]
    
    for slot_start, slot_end in all_busy_slots[1:]:
        if slot_start <= current_end:
            current_end = max(current_end, slot_end)
        else:
            merged_busy.append((current_start, current_end))
            current_start, current_end = slot_start, slot_end
    merged_busy.append((current_start, current_end))
    
    # Find free slots between busy periods
    free_slots = []
    current_time = start_time
    
    for busy_start, busy_end in merged_busy:
        if current_time < busy_start:
            free_slots.append({
                "start": current_time.isoformat(),
                "end": busy_start.isoformat()
            })
        current_time = busy_end
    
    if current_time < end_time:
        free_slots.append({
            "start": current_time.isoformat(),
            "end": end_time.isoformat()
        })
    
    return free_slots

@app.get("/calendar/events")
async def get_events(user_ids: str, find_free_slots: bool = False):
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
    
    if find_free_slots:
        free_slots = find_common_free_slots(all_events, now, week_later)
        return {"events": all_events, "free_slots": free_slots}
    
    return {"events": all_events}

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
                
                # 空き時間を計算
                free_slots = find_common_free_slots({"default_user": events}, now, week_later)
                
                calendar_info = "\n\n現在の予定:\n"
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    end = event['end'].get('dateTime', event['end'].get('date'))
                    calendar_info += f"- {event['summary']}: {start} から {end}\n"
                
                calendar_info += "\n\n空き時間:\n"
                for slot in free_slots:
                    start = datetime.fromisoformat(slot['start'].replace('Z', '+00:00'))
                    end = datetime.fromisoformat(slot['end'].replace('Z', '+00:00'))
                    calendar_info += f"- {start.strftime('%Y-%m-%d %H:%M')} から {end.strftime('%Y-%m-%d %H:%M')}\n"
                
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
async def google_auth(request: Request):
    backend_url = "https://backend-app-mkawqchd-1738594929.fly.dev"
    redirect_uri = f"{backend_url}/auth/google/callback"
    
    print(f"Starting OAuth flow with redirect_uri: {redirect_uri}")
    
    try:
        flow = Flow.from_client_secrets_file(
            "app/client_secrets.json",
            scopes=SCOPES
        )
        flow.redirect_uri = redirect_uri
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        print(f"Generated auth_url: {auth_url}")
        return {"auth_url": auth_url}
    except Exception as e:
        print(f"Error in OAuth flow: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/google/callback")
@app.post("/auth/google/callback")
async def google_auth_callback(request: Request, code: Optional[str] = None):
    try:
        # Handle GET request from Google OAuth redirect
        if not code:
            params = dict(request.query_params)
            code = params.get('code')
            if not code:
                raise HTTPException(status_code=400, detail="Authorization code not found")
        
        backend_url = "https://backend-app-mkawqchd-1738594929.fly.dev"
        redirect_uri = f"{backend_url}/auth/google/callback"
        
        flow = Flow.from_client_secrets_file(
            "app/client_secrets.json",
            scopes=SCOPES
        )
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Store credentials in memory (POC only)
        user_id = "default_user"  # In production, this would be a real user ID
        calendar_credentials[user_id] = credentials
        
        # Redirect back to the frontend after successful authentication
        return Response(
            content='<script>window.close();</script>',
            media_type='text/html'
        )
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"カレンダー情報の取得に失敗しました: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
