from fastapi import FastAPI, Request, HTTPException, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from fastapi_sessions.frontends.implementations import SessionCookie
from fastapi_sessions.backends.implementations import InMemoryBackend
from uuid import UUID, uuid4
import os
from dotenv import load_dotenv
from pydantic import BaseModel
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

class SessionData(BaseModel):
    id: UUID
    credentials: dict | None = None

cookie_name = "calendar_session"
backend = InMemoryBackend[UUID, SessionData]()
cookie = SessionCookie(
    cookie_name=cookie_name,
    identifier="general_verifier",
    auto_error=True,
    secret_key=os.getenv("SESSION_SECRET_KEY"),
    cookie_params={"secure": True, "httponly": True, "samesite": "lax"}
)

app = FastAPI()

# CORS設定
FRONTEND_URLS = [
    "https://google-calendar-bot-lb7lm5oq.devinapps.com",
    "http://localhost:3000"
]

@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    
    if request.method == "OPTIONS":
        response = Response()
        if origin in FRONTEND_URLS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Max-Age"] = "3600"
        return response
        
    response = await call_next(request)
    if origin in FRONTEND_URLS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response

# OAuth設定
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly'
]

def get_google_client_config():
    required_env_vars = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "BACKEND_URL", "FRONTEND_URL"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        raise HTTPException(
            status_code=500,
            detail=f"Missing required environment variables: {', '.join(missing_vars)}"
        )
    
    return {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{os.getenv('BACKEND_URL')}/auth/google/callback"],
            "javascript_origins": [os.getenv("FRONTEND_URL")]
        }
    }

@app.get("/auth/google")
async def google_auth():
    try:
        config = get_google_client_config()
        flow = Flow.from_client_config(
            config,
            scopes=SCOPES,
            redirect_uri=f"{os.getenv('BACKEND_URL')}/auth/google/callback"
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        return {"auth_url": authorization_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_session_data(
    session_id: UUID = Depends(cookie)
) -> SessionData:
    try:
        session = await backend.read(session_id)
        if session is None:
            raise HTTPException(status_code=401, detail="Session not found")
        return session
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid session")

@app.get("/auth/status")
async def auth_status(session_data: SessionData = Depends(get_session_data)):
    return {"authenticated": session_data.credentials is not None}

@app.get("/auth/google/callback")
async def auth_callback(request: Request, code: str):
    try:
        config = get_google_client_config()
        flow = Flow.from_client_config(
            config,
            scopes=SCOPES,
            redirect_uri=f"{os.getenv('BACKEND_URL')}/auth/google/callback"
        )
        
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        session_id = uuid4()
        session_data = SessionData(
            id=session_id,
            credentials={
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes
            }
        )
        
        await backend.create(session_id, session_data)
        
        return Response(
            content='''
                <script>
                    window.opener.postMessage({ type: 'AUTH_SUCCESS' }, '*');
                    window.close();
                </script>
            ''',
            media_type='text/html'
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class EventRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    summary: str = "Scheduled Meeting"

@app.post("/calendar/schedule")
async def schedule_event(
    event_data: EventRequest,
    session_data: SessionData = Depends(get_session_data)
):
    if not session_data.credentials:
        raise HTTPException(status_code=401, detail="認証が必要です")
    
    try:
        credentials = Credentials(
            token=session_data.credentials['token'],
            refresh_token=session_data.credentials['refresh_token'],
            token_uri=session_data.credentials['token_uri'],
            client_id=session_data.credentials['client_id'],
            client_secret=session_data.credentials['client_secret'],
            scopes=session_data.credentials['scopes']
        )
        
        calendar_service = build('calendar', 'v3', credentials=credentials)
        
        event = {
            'summary': event_data.summary,
            'start': {'dateTime': event_data.start_time.isoformat()},
            'end': {'dateTime': event_data.end_time.isoformat()},
            'timeZone': 'Asia/Tokyo'
        }
        
        try:
            created_event = calendar_service.events().insert(
                calendarId='primary',
                body=event
            ).execute()
            
            return {
                "success": True,
                "event_id": created_event["id"],
                "event_link": created_event.get("htmlLink")
            }
            
        except HttpError as error:
            raise HTTPException(
                status_code=500,
                detail=f"カレンダーイベントの作成に失敗しました: {str(error)}"
            )
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"予期せぬエラーが発生しました: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
