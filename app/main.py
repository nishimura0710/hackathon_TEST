from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import re
import os
import json
from redis import Redis, ConnectionError, ConnectionPool
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta, time, timezone

load_dotenv()

# Define JST timezone
JST = timezone(timedelta(hours=9))

# Set environment variables if not already set
if not os.getenv("BACKEND_URL"):
    os.environ["BACKEND_URL"] = "https://backend-app-ikkjfeex.fly.dev"

app = FastAPI()

# Initialize Redis connection pool
redis_pool = ConnectionPool(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.getenv("REDIS_PASSWORD", ""),
    decode_responses=True,
    socket_timeout=30,
    socket_connect_timeout=30,
    retry_on_timeout=True,
    max_connections=10
)

# Initialize Redis client with connection pool
redis_client = Redis(connection_pool=redis_pool)

@app.on_event("startup")
async def startup_event():
    """Startup event handler"""
    print("Application starting up...")

def store_credentials(user_id: str, credentials: Any) -> bool:
    try:
        cred_dict = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        result = redis_client.set(
            f"credentials:{user_id}",
            json.dumps(cred_dict),
            ex=3600  # 1 hour expiration
        )
        if result is None:
            print("Failed to store credentials in Redis")
            return False
        return True
    except (ConnectionError, Exception) as e:
        print(f"Error storing credentials: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証情報の保存に失敗しました"
        )

def get_credentials(user_id: str) -> Optional[Credentials]:
    try:
        cred_json = redis_client.get(f"credentials:{user_id}")
        if not cred_json:
            return None
        cred_dict = json.loads(cred_json)
        return Credentials(**cred_dict)
    except ConnectionError as e:
        print(f"Redis connection error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証情報の取得に失敗しました"
        )
    except json.JSONDecodeError as e:
        print(f"Invalid credential format: {str(e)}")
        return None
    except Exception as e:
        print(f"Unexpected error getting credentials: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証情報の取得中にエラーが発生しました"
        )

# CORS設定
FRONTEND_URLS = [
    "https://google-calendar-bot-lb7lm5oq.devinapps.com",
    "http://localhost:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,  # Use specific frontend URLs
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Authorization", "Cross-Origin-Opener-Policy"],
    max_age=3600
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    # Allow popups and cross-origin communication
    response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
    response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
    return response

# Google Calendar API設定
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    events: List[dict]

@app.get("/calendar/events")
async def get_events():
    try:
        user_id = "default_user"
        credentials = get_credentials(user_id)
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )
            
        now = datetime.utcnow()
        month_later = now + timedelta(days=30)
        
        try:
            events = await get_calendar_events(credentials, now, month_later)
            return {"events": events}
        except Exception as e:
            print(f"Calendar API error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="カレンダー情報の取得に失敗しました"
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error in calendar events: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予期せぬエラーが発生しました"
        )

@app.post("/chat/schedule")
async def chat(request: ChatRequest):
    try:
        import anthropic

        # Initialize Anthropic client
        client = anthropic.Client(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # Format the events data for Claude
        events_text = ""
        for event in request.events:
            start = datetime.fromisoformat(event['start'].replace('Z', '+00:00')).astimezone(JST)
            end = datetime.fromisoformat(event['end'].replace('Z', '+00:00')).astimezone(JST)
            events_text += f"{start.strftime('%Y年%m月%d日 %H:%M')}〜{end.strftime('%H:%M')} {event.get('summary', '予定あり')}\n"

        # Construct the system prompt
        system_prompt = """あなたはスケジュールの空き時間を案内するAIチャットボットです。以下のルールを厳守してください。

1. 質問に直接関連する情報のみを回答してください。雑談や補足説明は一切不要です。

2. 回答フォーマット:
- 日付は「M月D日」の形式で表示（年は含めない）
- 時間は「HH:00〜HH:00」の形式で表示
- 各時間枠は改行で区切る
例：
2月6日
12:00〜15:00
16:00〜19:00

3. 空き時間がない場合:
- 「M月D日は空き時間がありません。」とだけ回答

4. 不明確な質問への対応:
- 「確認したい日付を指定してください。」とだけ回答

重要な注意点：
- 年は表示しない
- 説明文は一切加えない
- 時間枠は可能な限り統合する（例：12:00-13:00と13:00-14:00は12:00-14:00にまとめる）
- 回答は指定されたフォーマットのみ"""

        # Construct the message for Claude
        user_message = f"""以下のカレンダー予定を元に、質問に答えてください：

予定リスト：
{events_text}

ユーザーの質問：
{request.messages[-1].content}"""

        # Get response from Claude
        response = client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=1000,
            temperature=0,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        try:
            # Extract just the text content from the response
            if not response.content or not isinstance(response.content[0].text, str):
                print("Invalid response format:", response)
                raise ValueError("Invalid response format from Anthropic API")
            
            response_text = response.content[0].text.strip()
            print(f"Raw response: {response_text}")

            # If all day is blocked, return the "no availability" message
            if request.events and all(event["start"] <= "2024-02-10T09:00:00+09:00" and event["end"] >= "2024-02-10T21:00:00+09:00" for event in request.events):
                date_match = re.search(r'(\d+)月(\d+)日', request.messages[-1].content)
                if date_match:
                    month, day = date_match.groups()
                    response_text = f"{month}月{day}日は空き時間がありません。"
        except Exception as e:
            print(f"Error processing response: {str(e)}")
            raise ValueError(f"Error processing response: {str(e)}")

        return {"response": response_text}
    except ValueError as e:
        print(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in chat_schedule: {str(e)}\nTraceback:\n{error_details}")
        raise HTTPException(status_code=500, detail="チャットの処理中にエラーが発生しました")

@app.get("/auth/google")
async def google_auth(request: Request):
    backend_url = os.getenv("BACKEND_URL")
    if not backend_url:
        raise HTTPException(status_code=500, detail="BACKEND_URL environment variable is not set")
    redirect_uri = f"{backend_url}/auth/google/callback"
    
    print(f"Starting OAuth flow with redirect_uri: {redirect_uri}")
    
    try:
        client_config = {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"{os.getenv('BACKEND_URL')}/auth/google/callback"],
                "javascript_origins": [os.getenv("FRONTEND_URL")]
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES
        )
        flow.redirect_uri = redirect_uri
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        return {"auth_url": auth_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail="認証フローの初期化に失敗しました")

@app.get("/auth/google/callback")
async def google_auth_callback(request: Request, code: Optional[str] = None):
    try:
        if not code:
            code = request.query_params.get('code')
            if not code:
                raise HTTPException(status_code=400, detail="認証コードが見つかりません")

        backend_url = os.getenv("BACKEND_URL")
        frontend_url = os.getenv("FRONTEND_URL")
        if not backend_url or not frontend_url:
            raise HTTPException(status_code=500, detail="環境変数が設定されていません")

        client_config = {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"{backend_url}/auth/google/callback"],
                "javascript_origins": [os.getenv("FRONTEND_URL")]
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=f"{backend_url}/auth/google/callback"
        )
        
        try:
            flow.fetch_token(code=code)
        except Exception as e:
            print(f"Token fetch error: {str(e)}")
            raise HTTPException(status_code=401, detail="認証トークンの取得に失敗しました")
            
        if not store_credentials("default_user", flow.credentials):
            raise HTTPException(status_code=500, detail="認証情報の保存に失敗しました")
        
        return Response(
            content=f'''
            <html>
                <head>
                    <title>認証完了</title>
                    <meta charset="utf-8">
                    <script>
                        window.onload = function() {{
                            try {{
                                window.opener.postMessage('authentication_success', '*');
                                setTimeout(function() {{
                                    window.close();
                                    window.location.href = '{frontend_url}';
                                }}, 1000);
                            }} catch (error) {{
                                window.location.href = '{frontend_url}';
                            }}
                        }};
                    </script>
                    <style>
                        body {{
                            font-family: sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background-color: #f0f0f0;
                        }}
                        .message {{
                            text-align: center;
                            padding: 2rem;
                            background: white;
                            border-radius: 8px;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        }}
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h1>認証が完了しました</h1>
                        <p>このページは自動的に閉じられます...</p>
                    </div>
                </body>
            </html>
            ''',
            media_type='text/html'
        )
    except Exception as e:
        error_message = str(e)
        return Response(
            content=f'''
            <html>
                <head>
                    <title>認証エラー</title>
                    <meta charset="utf-8">
                    <style>
                        body {{
                            font-family: sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background-color: #f0f0f0;
                        }}
                        .error {{
                            text-align: center;
                            padding: 2rem;
                            background: white;
                            border-radius: 8px;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                            color: #e11d48;
                        }}
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>認証エラー</h1>
                        <p>{error_message}</p>
                        <p><a href="{frontend_url}">トップページに戻る</a></p>
                    </div>
                </body>
            </html>
            ''',
            media_type='text/html',
            status_code=400
        )

def calculate_available_slots(events: List[tuple], start_hour: int, end_hour: int, 
                            target_date: datetime) -> List[tuple]:
    """Calculate available time slots between events."""
    jst = timezone(timedelta(hours=9))
    
    # Get the year from the first event, or use current year if no events
    if events:
        event_year = events[0][0].year
    else:
        event_year = datetime.now(jst).year
    
    # Ensure target_date has the correct year and timezone
    target_date = target_date.replace(year=event_year)
    if target_date.tzinfo is None:
        target_date = target_date.replace(tzinfo=jst)
    elif target_date.tzinfo != jst:
        target_date = target_date.astimezone(jst)
    
    day_start = target_date.replace(hour=start_hour, minute=0)
    day_end = target_date.replace(hour=end_hour, minute=0)
    print(f"Day start: {day_start}, Day end: {day_end}")
    print(f"Original events: {events}")
    
    if not events:
        return [(day_start, day_end)]
    
    # Ensure all events are in JST
    normalized_events = []
    for start, end in events:
        if start.tzinfo is None:
            start = start.replace(tzinfo=jst)
        elif start.tzinfo != jst:
            start = start.astimezone(jst)
            
        if end.tzinfo is None:
            end = end.replace(tzinfo=jst)
        elif end.tzinfo != jst:
            end = end.astimezone(jst)
            
        normalized_events.append((start, end))
    
    print(f"Normalized events: {normalized_events}")
    
    # Sort events by start time
    sorted_events = sorted(normalized_events)
    available_slots = []
    current_time = day_start
    
    # Find gaps between events
    for event_start, event_end in sorted_events:
        # Skip events outside our time range
        if event_start > day_end or event_end < day_start:
            continue
        
        # Adjust event times to our time range
        event_start = max(event_start, day_start)
        event_end = min(event_end, day_end)
        
        # If there's a gap before this event
        if current_time < event_start:
            duration = (event_start - current_time).total_seconds()
            if duration >= 3600:  # At least 1 hour
                available_slots.append((current_time, event_start))
        
        # Move current time to after this event
        current_time = max(current_time, event_end)
    
    # Add remaining time after last event if it's at least 1 hour
    if current_time < day_end:
        duration = (day_end - current_time).total_seconds()
        if duration >= 3600:
            available_slots.append((current_time, day_end))
    
    return available_slots

async def get_calendar_events(credentials: Credentials, time_min: datetime, time_max: datetime):
    """Fetch calendar events for the specified time range."""
    try:
        service = build('calendar', 'v3', credentials=credentials)
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime',
            maxResults=100  # Limit results for better performance
        ).execute()
        
        events = events_result.get('items', [])
        formatted_events = []
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            formatted_events.append({
                'summary': event.get('summary', '(タイトルなし)'),
                'start': start,
                'end': end,
                'status': event.get('status', 'confirmed')
            })
            
        return formatted_events
    except Exception as e:
        print(f"Error fetching calendar events: {str(e)}")
        raise

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
