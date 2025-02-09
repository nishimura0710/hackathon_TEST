from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import re
import os
from dotenv import load_dotenv
from claude_service import ClaudeService

load_dotenv()

app = FastAPI()
claude_service = ClaudeService()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# カレンダーAPI設定
SCOPES = ['https://www.googleapis.com/auth/calendar']

class ChatMessage(BaseModel):
    message: str

def get_calendar_service():
    """Get an authenticated Google Calendar service."""
    credentials = service_account.Credentials.from_service_account_file(
        'app/service_account.json',
        scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=credentials)

def get_free_busy(service, calendar_id, start_time, end_time):
    """Get busy slots for the specified time range."""
    body = {
        'timeMin': start_time.isoformat(),
        'timeMax': end_time.isoformat(),
        'timeZone': 'Asia/Tokyo',
        'items': [{'id': calendar_id}]
    }
    response = service.freebusy().query(body=body).execute()
    return response['calendars'][calendar_id]['busy']

def create_event(service, calendar_id, slot):
    """Create a calendar event."""
    event = {
        'summary': '会議',
        'start': {
            'dateTime': slot['suggested_time']['start'],
            'timeZone': 'Asia/Tokyo'
        },
        'end': {
            'dateTime': slot['suggested_time']['end'],
            'timeZone': 'Asia/Tokyo'
        }
    }
    return service.events().insert(calendarId=calendar_id, body=event).execute()

@app.post("/calendar/chat")
async def handle_chat(message: ChatMessage):
    """Handle chat messages for calendar scheduling."""
    try:
        # Get calendar service
        calendar_service = get_calendar_service()
        calendar_id = os.getenv('CALENDAR_ID', 'us.tomoki17@gmail.com')
        
        # Extract date and time from message using Claude
        response = claude_service.analyze_free_slots(
            [],  # Empty busy slots for initial time parsing
            datetime.now(),
            datetime.now() + timedelta(days=30),
            calendar_id
        )
        
        if not response:
            return {
                "response": "申し訳ありません。日時の指定を理解できませんでした。\n"
                           "以下のような形式で指定してください：\n"
                           "・2月12日の13時から16時\n"
                           "・明日の午前10時から午後3時\n"
                           "・来週の月曜日の15時から17時"
            }
        
        # Get busy slots for the specified time range
        start_time = datetime.fromisoformat(response['suggested_time']['start'])
        end_time = datetime.fromisoformat(response['suggested_time']['end'])
        
        # Validate business hours (9:00-18:00)
        if start_time.hour < 9 or end_time.hour > 18:
            return {
                "response": "申し訳ありません。営業時間外の時間帯が指定されています。\n"
                           "営業時間（9時から18時）内の時間帯を指定してください。"
            }
        
        # Get busy slots and find best time
        busy_slots = get_free_busy(calendar_service, calendar_id, start_time, end_time)
        slot = claude_service.analyze_free_slots(busy_slots, start_time, end_time, calendar_id)
        
        if not slot:
            return {
                "response": "申し訳ありません。指定された時間範囲内に適切な空き時間が見つかりませんでした。\n"
                           "別の時間帯をお試しください。"
            }
        
        # Create event
        try:
            created_event = create_event(calendar_service, calendar_id, slot)
            return {
                "response": f"以下の時間に会議を登録しました：\n"
                           f"{slot['reason']}\n"
                           f"予定のリンク：{created_event.get('htmlLink')}"
            }
        except Exception as e:
            print(f"Event creation error: {str(e)}")
            return {
                "response": "申し訳ありません。予定の登録に失敗しました。\n"
                           "別の時間帯を指定してください。"
            }
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "response": "申し訳ありません。予定の登録に失敗しました。\n"
                       "別の時間帯を指定してください。"
        }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
