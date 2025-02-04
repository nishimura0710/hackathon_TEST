from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import anthropic
import os
import json
from redis import Redis, ConnectionError, ConnectionPool
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta

load_dotenv()

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
    """Verify Redis connection on application startup"""
    try:
        redis_client.ping()
        print("Redis connection successful")
    except ConnectionError as e:
        print(f"Redis connection failed: {str(e)}")
        raise Exception("Failed to connect to Redis")
    except Exception as e:
        print(f"Unexpected error during Redis connection: {str(e)}")
        raise

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
        return redis_client.set(
            f"credentials:{user_id}",
            json.dumps(cred_dict),
            ex=3600  # 1 hour expiration
        )
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
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")
        
        user_id = "default_user"
        credentials = get_credentials(user_id)
        if not credentials:
            raise HTTPException(status_code=401, detail="カレンダーの認証が必要です")
        
        calendar_info = "\n\n今後1ヶ月の予定:\n"
        for event in request.events:
            start_time = datetime.fromisoformat(event['start'].replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(event['end'].replace('Z', '+00:00'))
            calendar_info += f"- {event['summary']}: {start_time.strftime('%Y-%m-%d %H:%M')} から {end_time.strftime('%Y-%m-%d %H:%M')}\n"
        
        client = anthropic.AsyncAnthropic(api_key=api_key)
        
        system_prompt = """あなたはカレンダーアシスタントです。
ユーザーの予定に関する質問に答え、空き時間を見つける手助けをしてください。
日本語で簡潔かつ親しみやすい応答をしてください。"""

        user_message = request.messages[-1].content + calendar_info
        
        try:
            response = await client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=1024,
                messages=[{"role": "user", "content": user_message}],
                system=system_prompt
            )
            if not response.content:
                return {"response": "応答を生成できませんでした"}
            
            # Handle the Claude-3 response format
            if not response.content or len(response.content) == 0:
                return {"response": "応答を生成できませんでした"}
                
            # Claude-3 returns a list of content blocks
            for content in response.content:
                if content.type == 'text':
                    return {"response": content.text}
            
            return {"response": "応答を生成できませんでした"}
                
        except Exception as api_error:
            raise HTTPException(status_code=500, detail="チャットの処理中にエラーが発生しました")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
