from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel
import redis
import json
import os
import anthropic
import pytz

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "https://google-calendar-bot-lb7lm5oq.devinapps.com")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis client setup
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    password=os.getenv("REDIS_PASSWORD", ""),
    decode_responses=True
)

# Anthropic client setup
claude = anthropic.Client(api_key=os.getenv("ANTHROPIC_API_KEY"))

async def get_calendar_list(credentials: Credentials) -> List[dict]:
    """Fetch list of all accessible calendars."""
    try:
        service = build('calendar', 'v3', credentials=credentials)
        calendar_list = service.calendarList().list().execute()
        return calendar_list.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar list: {str(e)}")
        raise

async def get_calendar_events(credentials: Credentials, time_min: datetime, time_max: datetime):
    """Fetch calendar events for the specified time range from all accessible calendars."""
    try:
        service = build('calendar', 'v3', credentials=credentials)
        calendars = await get_calendar_list(credentials)
        
        all_events = []
        for calendar in calendars:
            calendar_id = calendar['id']
            try:
                # Skip calendars we can't access
                if calendar.get('accessRole') not in ['owner', 'writer', 'reader']:
                    print(f"Skipping calendar {calendar_id} due to insufficient access rights")
                    continue
                    
                # Ensure timezone is properly formatted in RFC3339 format
                time_min_str = time_min.astimezone(pytz.UTC).isoformat()
                time_max_str = time_max.astimezone(pytz.UTC).isoformat()
                
                print(f"\nFetching events for calendar: {calendar_id}")
                print(f"Access role: {calendar.get('accessRole')}")
                print(f"Time range: {time_min_str} to {time_max_str}")
                
                try:
                    events_result = service.events().list(
                        calendarId=calendar_id,
                        timeMin=time_min_str,
                        timeMax=time_max_str,
                        singleEvents=True,
                        orderBy='startTime',
                        maxResults=100
                    ).execute()
                except Exception as e:
                    print(f"Error fetching events for calendar {calendar_id}: {str(e)}")
                    continue
                
                events = events_result.get('items', [])
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    end = event['end'].get('dateTime', event['end'].get('date'))
                    all_events.append({
                        'summary': event.get('summary', '(タイトルなし)'),
                        'start': start,
                        'end': end,
                        'status': event.get('status', 'confirmed'),
                        'calendar': calendar.get('summary', 'Unknown Calendar')
                    })
            except Exception as e:
                print(f"Error fetching events for calendar {calendar_id}: {str(e)}")
                continue
                
        return all_events
    except Exception as e:
        print(f"Error fetching calendar events: {str(e)}")
        raise

def store_credentials(user_id: str, credentials: dict):
    """Store user credentials in Redis."""
    try:
        redis_client.set(f"credentials:{user_id}", json.dumps(credentials))
    except Exception as e:
        print(f"Error storing credentials: {str(e)}")
        raise

def get_credentials(user_id: str) -> Optional[Credentials]:
    """Retrieve user credentials from Redis."""
    try:
        creds_json = redis_client.get(f"credentials:{user_id}")
        if not creds_json:
            return None
        creds_dict = json.loads(creds_json)
        return Credentials.from_authorized_user_info(creds_dict)
    except Exception as e:
        print(f"Error retrieving credentials: {str(e)}")
        return None

@app.get("/auth/google")
async def google_auth():
    """Initiate Google OAuth flow."""
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            'app/client_secrets.json',
            scopes=['https://www.googleapis.com/auth/calendar.readonly'],
            redirect_uri=f"{os.getenv('BACKEND_URL', '')}/auth/google/callback"
        )
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        return {"auth_url": auth_url}
    except Exception as e:
        print(f"Error in Google auth: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証の初期化に失敗しました"
        )

@app.get("/auth/google/callback")
async def google_auth_callback(code: str, state: str):
    """Handle Google OAuth callback."""
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            'app/client_secrets.json',
            scopes=['https://www.googleapis.com/auth/calendar.readonly'],
            redirect_uri=f"{os.getenv('BACKEND_URL', '')}/auth/google/callback"
        )
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        creds_dict = json.loads(credentials.to_json())
        
        store_credentials("default_user", creds_dict)
        frontend_url = os.getenv('FRONTEND_URL', '')
        return RedirectResponse(url=frontend_url)
    except Exception as e:
        print(f"Error in Google auth callback: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="認証コールバックの処理に失敗しました"
        )

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]

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
async def chat_schedule(request: ChatRequest):
    try:
        user_id = "default_user"
        credentials = get_credentials(user_id)
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )

        # Extract date and time from the last message
        last_message = request.messages[-1].content
        
        # Default to today's date in JST
        jst_now = datetime.utcnow() + timedelta(hours=9)
        target_date = jst_now
        
        # Parse date from message
        if "月" in last_message and "日" in last_message:
            try:
                # Extract numbers before 月 and 日
                month_idx = last_message.index("月")
                day_idx = last_message.index("日")
                
                # Look for numbers before these markers
                month_str = ""
                day_str = ""
                
                for i in range(month_idx - 1, -1, -1):
                    if last_message[i].isdigit():
                        month_str = last_message[i] + month_str
                    else:
                        break
                        
                for i in range(day_idx - 1, month_idx, -1):
                    if last_message[i].isdigit():
                        day_str = last_message[i] + day_str
                    else:
                        break
                
                if not month_str or not day_str:
                    return {"response": "確認したい日付を指定してください。"}
                    
                month = int(month_str)
                day = int(day_str)
                target_date = datetime(jst_now.year, month, day)
            except (ValueError, IndexError):
                return {"response": "確認したい日付を指定してください。"}
        else:
            return {"response": "確認したい日付を指定してください。"}
            
        # Set default time range (full day in UTC)
        jst = pytz.timezone('Asia/Tokyo')
        utc = pytz.UTC
        
        # Create timezone-aware datetime objects
        start_time = jst.localize(datetime.combine(target_date.date(), datetime.min.time())).astimezone(utc)
        end_time = jst.localize(datetime.combine(target_date.date(), datetime.max.time())).astimezone(utc)
        
        # Parse time range if specified
        if "時" in last_message:
            try:
                # Extract start and end hours
                start_hour = None
                end_hour = None
                
                # Look for patterns like "13時から17時" or "13時〜17時"
                parts = last_message.split("時")
                for i, part in enumerate(parts[:-1]):
                    # Extract the number before "時"
                    num = ""
                    for char in reversed(part):
                        if char.isdigit():
                            num = char + num
                        else:
                            break
                    
                    if num:
                        hour = int(num)
                        if "から" in part or "～" in part or "〜" in part:
                            start_hour = hour
                        elif i + 1 < len(parts) and ("まで" in parts[i+1] or "迄" in parts[i+1]):
                            end_hour = hour
                
                if start_hour is not None:
                    start_time = jst.localize(
                        datetime.combine(target_date.date(), datetime.min.time().replace(hour=start_hour))
                    ).astimezone(utc)
                if end_hour is not None:
                    end_time = jst.localize(
                        datetime.combine(target_date.date(), datetime.min.time().replace(hour=end_hour))
                    ).astimezone(utc)
            except ValueError as e:
                print(f"Error parsing time range: {str(e)}")
                # Continue with default full day range

        # Fetch events from all calendars
        try:
            print(f"\nFetching events for {target_date.strftime('%Y-%m-%d')}")
            print(f"Time range (UTC): {start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}")
            events = await get_calendar_events(credentials, start_time, end_time)
            print(f"Found {len(events)} events across all calendars")
            for event in events:
                print(f"Event: {event.get('summary')} from {event.get('calendar')}")
                print(f"  Time: {event['start']} - {event['end']}")
        except Exception as e:
            print(f"Error fetching calendar events: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="カレンダー情報の取得に失敗しました"
            )
        
        # Convert events to JST and sort
        print("\nConverting events to JST...")
        jst_events = []
        for event in events:
            try:
                # Parse datetime with proper timezone handling
                start_str = event['start'] if isinstance(event['start'], str) else event['start'].get('dateTime', event['start'].get('date'))
                end_str = event['end'] if isinstance(event['end'], str) else event['end'].get('dateTime', event['end'].get('date'))
                
                print(f"\nProcessing event: {event.get('summary')} from {event.get('calendar')}")
                print(f"Raw times: {start_str} - {end_str}")
                
                # Handle both full datetime and date-only formats
                if 'T' in start_str:  # Full datetime
                    event_start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                else:  # Date only
                    event_start = datetime.fromisoformat(start_str + 'T00:00:00+00:00')
                    
                if 'T' in end_str:  # Full datetime
                    event_end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                else:  # Date only
                    event_end = datetime.fromisoformat(end_str + 'T23:59:59+00:00')
                
                # Convert to JST
                event_start = event_start + timedelta(hours=9)
                event_end = event_end + timedelta(hours=9)
                print(f"JST times: {event_start.strftime('%Y-%m-%d %H:%M')} - {event_end.strftime('%Y-%m-%d %H:%M')}")
                
                jst_events.append({
                    'start': event_start,
                    'end': event_end,
                    'summary': event.get('summary'),
                    'calendar': event.get('calendar')
                })
            except Exception as e:
                print(f"Error processing event: {str(e)}")
                continue
        
        jst_events.sort(key=lambda x: x['start'])
        
        # Calculate available slots in JST
        print("\nCalculating available slots...")
        available_slots = []
        current_time = start_time + timedelta(hours=9)
        end_time_jst = end_time + timedelta(hours=9)
        
        # Ensure we respect the requested time range
        target_date_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        target_date_end = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # If specific hours were requested, adjust the time range
        if 'start_hour' in locals() and start_hour is not None:
            current_time = target_date_start.replace(hour=int(start_hour))
            if 'end_hour' in locals() and end_hour is not None:
                end_time_jst = target_date_start.replace(hour=int(end_hour))
        
        print(f"Time range (JST): {current_time.strftime('%Y-%m-%d %H:%M')} - {end_time_jst.strftime('%Y-%m-%d %H:%M')}")
        
        # Sort events by start time
        jst_events.sort(key=lambda x: x['start'])
        print(f"\nProcessing {len(jst_events)} events in chronological order")
        
        # If no events, return the requested time range
        if not jst_events:
            print("No events found, returning requested time range")
            available_slots = [(current_time, end_time_jst)]
            meaningful_slots = [(current_time, end_time_jst)]  # Also set meaningful_slots to avoid NoneType error
        else:
            # Find available slots between events
            for event in jst_events:
                print(f"\nChecking event: {event.get('summary')} from {event.get('calendar')}")
                print(f"Event time: {event['start'].strftime('%Y-%m-%d %H:%M')} - {event['end'].strftime('%Y-%m-%d %H:%M')}")
                
                # Skip events outside our time range
                if event['end'] <= current_time:
                    print("Event ends before current time, skipping")
                    continue
                if event['start'] >= end_time_jst:
                    print("Event starts after end time, breaking")
                    break
                
                # If there's a gap before this event
                if current_time < event['start']:
                    slot_duration = (event['start'] - current_time).total_seconds() / 60
                    print(f"Found potential slot: {current_time.strftime('%H:%M')} - {event['start'].strftime('%H:%M')} ({slot_duration:.0f} minutes)")
                    # Only add slots that are at least 30 minutes
                    if slot_duration >= 30:
                        available_slots.append((current_time, event['start']))
                        print("Added slot (≥30 minutes)")
                    else:
                        print("Slot too short (<30 minutes), skipping")
                
                # Update current_time to end of this event
                current_time = max(current_time, event['end'])
                print(f"Current time now: {current_time.strftime('%H:%M')}")
            
            # Add final slot if there's time after the last event
            if current_time < end_time_jst:
                slot_duration = (end_time_jst - current_time).total_seconds() / 60
                print(f"Found final slot: {current_time.strftime('%H:%M')} - {end_time_jst.strftime('%H:%M')} ({slot_duration:.0f} minutes)")
                if slot_duration >= 30:
                    available_slots.append((current_time, end_time_jst))
                    print("Added final slot (≥30 minutes)")
                else:
                    print("Final slot too short (<30 minutes), skipping")
        
        # Filter out slots shorter than 30 minutes and ensure they're within the requested time range
        meaningful_slots = []
        for start, end in available_slots:
            # Clip the slot to the requested time range
            start = max(start, current_time)
            end = min(end, end_time_jst)
            
            if (end - start).total_seconds() >= 1800:
                # Only include slots that fall within the target date and time range
                if start.date() == target_date.date():
                    meaningful_slots.append((start, end))
                    print(f"Added meaningful slot: {start.strftime('%H:%M')} - {end.strftime('%H:%M')}")
        
        if not meaningful_slots:
            print("No meaningful slots found")
            return {"response": f"{month}月{day}日は空き時間がありません。"}
        
        # Format response
        response_lines = [f"{month}月{day}日"]
        for start, end in meaningful_slots:
            response_lines.append(f"{start.strftime('%H:%M')}〜{end.strftime('%H:%M')}")
        
        print(f"Final response: {response_lines}")
        return {"response": "\n".join(response_lines)}
        
    except Exception as e:
        print(f"Error in chat schedule: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予期せぬエラーが発生しました"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
