from fastapi import APIRouter, HTTPException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
from .redis_config import redis_client
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

def get_calendar_service():
    try:
        creds_json = redis_client.get('credentials:default_user')
        if not creds_json:
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )
        
        creds_dict = json.loads(creds_json)
        credentials = Credentials(**creds_dict)
        return build('calendar', 'v3', credentials=credentials)
    except json.JSONDecodeError:
        logger.error("Invalid credentials format in Redis")
        raise HTTPException(
            status_code=401,
            detail="カレンダーの認証が必要です"
        )
    except Exception as e:
        logger.error(f"Error creating calendar service: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="カレンダーサービスの初期化に失敗しました"
        )

@router.get("/events")
async def list_events():
    try:
        service = get_calendar_service()
        now = datetime.now(ZoneInfo("Asia/Tokyo"))
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=30)).isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        formatted_events = [
            {
                "summary": event.get('summary', '(タイトルなし)'),
                "start": event['start'].get('dateTime', event['start'].get('date')),
                "end": event['end'].get('dateTime', event['end'].get('date'))
            }
            for event in events
        ]
        
        return {"events": formatted_events}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching calendar events: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予定の取得に失敗しました"
        )
