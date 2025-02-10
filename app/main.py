from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
from dotenv import load_dotenv
from claude_service import ClaudeService
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()
claude_service = ClaudeService()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

SCOPES = ['https://www.googleapis.com/auth/calendar']

class ChatMessage(BaseModel):
    message: str

@app.post("/calendar/chat")
async def handle_chat(message: ChatMessage):
    try:
        service = build('calendar', 'v3', 
            credentials=service_account.Credentials.from_service_account_file(
                'service_account.json', scopes=SCOPES
            )
        )
        calendar_id = os.getenv('CALENDAR_ID', 'us.tomoki17@gmail.com')
        
        # Get tomorrow's date for the requested time (in JST)
        jst = timezone(timedelta(hours=9))  # JST = UTC+9
        now = datetime.now(tz=jst)
        tomorrow = now + timedelta(days=1)
        # Parse time from message (default to 14:00 if not specified)
        hour = 14  # Default hour
        message_text = message.message
        
        # Handle different time formats
        if "午後" in message_text:
            if "4時" in message_text or "４時" in message_text:
                hour = 16
            elif "3時" in message_text or "３時" in message_text:
                hour = 15
            elif "2時" in message_text or "２時" in message_text:
                hour = 14
            elif "1時" in message_text or "１時" in message_text:
                hour = 13
        elif "14時" in message_text:
            hour = 14
        elif "15時" in message_text:
            hour = 15
        elif "16時" in message_text:
            hour = 16
        elif "13時" in message_text:
            hour = 13
            
        # Create datetime objects with JST timezone
        start_time = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            hour=hour, minute=0, second=0, microsecond=0, tzinfo=jst
        )
        end_time = start_time + timedelta(hours=1)
        
        # Get busy slots for the specific time range
        # Query with a wider range to ensure we catch all conflicts
        query_start_utc = (start_time - timedelta(minutes=15)).astimezone(timezone.utc)
        query_end_utc = (end_time + timedelta(minutes=15)).astimezone(timezone.utc)
        
        # Query with UTC times but JST timezone
        query_body = {
            'timeMin': query_start_utc.isoformat().replace('+00:00', 'Z'),
            'timeMax': query_end_utc.isoformat().replace('+00:00', 'Z'),
            'timeZone': 'Asia/Tokyo',
            'items': [{'id': calendar_id}]
        }
        print(f"Query body: {json.dumps(query_body, indent=2)}")
        
        freebusy = service.freebusy().query(body=query_body).execute()
        print(f"Freebusy response: {json.dumps(freebusy, indent=2)}")
        
        if calendar_id not in freebusy.get('calendars', {}):
            print(f"Calendar {calendar_id} not found in freebusy response")
            return {"response": "申し訳ありません。カレンダーへのアクセスに問題が発生しました。"}
            
        busy_slots = freebusy['calendars'][calendar_id]['busy']
        print(f"Busy slots: {json.dumps(busy_slots, indent=2)}")
        print(f"Calendar ID: {calendar_id}")
        
        # Use claude_service to find available slots
        result = claude_service.analyze_free_slots(
            busy_slots=busy_slots,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id
        )
        
        if not result.get('suggested_time'):
            return {"response": result.get('reason')}
            
        # Create event using suggested time
        suggested_start = datetime.fromisoformat(result['suggested_time']['start'])
        suggested_end = datetime.fromisoformat(result['suggested_time']['end'])
        
        event = {
            'summary': '会議',
            'start': {
                'dateTime': suggested_start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'timeZone': 'Asia/Tokyo'
            },
            'end': {
                'dateTime': suggested_end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'timeZone': 'Asia/Tokyo'
            },
            'reminders': {
                'useDefault': True
            }
        }
        
        try:
            created = service.events().insert(calendarId=calendar_id, body=event).execute()
            jst_time = suggested_start.strftime("%Y年%m月%d日 %H時%M分")
            return {
                "response": f"{result.get('reason')}\n"
                           f"予定のリンク：{created.get('htmlLink')}"
            }
        except Exception:
            return {"response": "申し訳ありません。予定の登録中にエラーが発生しました。別の時間帯をお試しください。"}
    except Exception as e:
        print(f"Error: {str(e)}")
        return {"response": "申し訳ありません。予定の登録に失敗しました。"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
