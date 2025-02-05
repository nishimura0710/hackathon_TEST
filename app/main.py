from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, root_validator, validator
import redis
import json
import os
import re
import anthropic
import pytz

class EventCreateRequest(BaseModel):
    date: str
    start_time: str
    end_time: str
    title: str
    description: Optional[str] = None

    @validator('date')
    def validate_date(cls, v):
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            raise ValueError('日付の形式が正しくありません')

    @validator('start_time', 'end_time')
    def validate_time(cls, v):
        try:
            datetime.strptime(v, '%H:%M')
            return v
        except ValueError:
            raise ValueError('時間の形式が正しくありません')

class Message(BaseModel):
    role: str
    content: str

class EventMessage(Message, EventCreateRequest):
    pass

class ChatRequest(BaseModel):
    messages: Optional[List[Message]] = None
    message: Optional[str] = None
    events: Optional[List[dict]] = None

    @root_validator(pre=True)
    def check_message_format(cls, values):
        if not values.get("messages") and not values.get("message"):
            raise ValueError("メッセージが見つかりません")
        if not values.get("messages"):
            values["messages"] = [Message(
                role="user",
                content=values["message"]
            )]
        return values

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

async def create_calendar_event(service, event_data: EventCreateRequest):
    """Create a new calendar event."""
    try:
        date = datetime.strptime(event_data.date, '%Y-%m-%d')
        start_time = datetime.strptime(event_data.start_time, '%H:%M')
        end_time = datetime.strptime(event_data.end_time, '%H:%M')
        
        start_datetime = date.replace(
            hour=start_time.hour,
            minute=start_time.minute
        )
        end_datetime = date.replace(
            hour=end_time.hour,
            minute=end_time.minute
        )
        
        jst = pytz.timezone('Asia/Tokyo')
        start_datetime = jst.localize(start_datetime)
        end_datetime = jst.localize(end_datetime)
        
        event = {
            'summary': event_data.title,
            'description': event_data.description,
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': 'Asia/Tokyo',
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': 'Asia/Tokyo',
            },
        }
        
        event = service.events().insert(calendarId='primary', body=event).execute()
        return event
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"予定の登録ができませんでした：{str(e)}"
        )

async def get_calendar_events(credentials: Credentials, time_min: datetime, time_max: datetime):
    """Fetch calendar events for the specified time range from all accessible calendars."""
    try:
        service = build('calendar', 'v3', credentials=credentials)
        calendars = await get_calendar_list(credentials)
        
        all_events = []
        for calendar in calendars:
            calendar_id = calendar['id']
            try:
                if calendar.get('accessRole') not in ['owner', 'writer', 'reader']:
                    print(f"Skipping calendar {calendar_id} due to insufficient access rights")
                    continue
                
                time_min_utc = time_min.astimezone(pytz.UTC)
                time_max_utc = time_max.astimezone(pytz.UTC)
                
                print(f"\nFetching events for calendar: {calendar_id}")
                print(f"Access role: {calendar.get('accessRole')}")
                print(f"Time range: {time_min_utc} to {time_max_utc}")
                
                try:
                    events_result = service.events().list(
                        calendarId=calendar_id,
                        timeMin=time_min_utc.isoformat(),
                        timeMax=time_max_utc.isoformat(),
                        singleEvents=True,
                        orderBy='startTime',
                        maxResults=100
                    ).execute()
                except Exception as e:
                    print(f"Error fetching events for calendar {calendar_id}: {str(e)}")
                    continue
                
                jst = pytz.timezone('Asia/Tokyo')
                events = events_result.get('items', [])
                for event in events:
                    try:
                        start_data = event['start']
                        end_data = event['end']
                        
                        jst = pytz.timezone('Asia/Tokyo')
                        if 'dateTime' in start_data:
                            start = datetime.fromisoformat(start_data['dateTime'].replace('Z', '+00:00'))
                            if not start.tzinfo:
                                start = pytz.UTC.localize(start)
                            start = start.astimezone(jst)
                            
                            end = datetime.fromisoformat(end_data['dateTime'].replace('Z', '+00:00'))
                            if not end.tzinfo:
                                end = pytz.UTC.localize(end)
                            end = end.astimezone(jst)
                        else:
                            start = datetime.fromisoformat(start_data['date'])
                            start = jst.localize(start.replace(hour=0, minute=0))
                            end = datetime.fromisoformat(end_data['date'])
                            end = jst.localize(end.replace(hour=23, minute=59))
                        
                        all_events.append({
                            'summary': event.get('summary', '(タイトルなし)'),
                            'start': start,
                            'end': end,
                            'status': event.get('status', 'confirmed'),
                            'calendar': calendar.get('summary', 'Unknown Calendar')
                        })
                    except Exception as e:
                        print(f"Error processing event: {str(e)}")
                        continue
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
            scopes=['https://www.googleapis.com/auth/calendar.readonly', 'https://www.googleapis.com/auth/calendar.events'],
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
            scopes=['https://www.googleapis.com/auth/calendar.readonly', 'https://www.googleapis.com/auth/calendar.events'],
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
        print("\n=== Chat Schedule Request ===")
        print(f"Request messages: {json.dumps(request.dict(), ensure_ascii=False, indent=2)}")
        user_id = "default_user"
        credentials = get_credentials(user_id)
        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )

        # Extract date and time from the message
        if not request.messages:
            return {"response": "メッセージを入力してください。"}
        last_message = request.messages[-1].content
        
        # Check for event creation commands
        available_slot_pattern = re.compile(r'(\d+)月(\d+)日の空いている時間に(.+)を登録して')
        event_match = re.match(
            r'(\d+)月(\d+)日の(\d+)時から(\d+)時まで(.+)を登録して',
            last_message
        )
        print(f"\nChecking event creation command: {last_message}")
        print(f"Regex match result: {event_match is not None}")
        available_slot_match = available_slot_pattern.match(last_message)
        print(f"Available slot match result: {available_slot_match is not None}")
        
        if available_slot_match:
            month, day, title = available_slot_match.groups()
            month, day = map(int, [month, day])
            
            # Get current year and handle year rollover
            now = datetime.now(pytz.timezone('Asia/Tokyo'))
            year = now.year
            if month < now.month:
                year += 1
                
            # Set up target date
            target_date = now.replace(year=year, month=month, day=day)
            start_time = target_date.replace(hour=0, minute=0)
            end_time = target_date.replace(hour=23, minute=59)
            
            # Get available slots
            events = await get_calendar_events(credentials, start_time, end_time)
            
            # Set business hours
            business_start = target_date.replace(hour=9, minute=0)
            business_end = target_date.replace(hour=17, minute=0)
            
            if not events:
                # No events, use first business hour
                event_start = business_start
                event_end = event_start + timedelta(hours=1)
            else:
                # Find first available slot of at least 1 hour during business hours
                current_time = business_start
                event_start = None
                event_end = None
                
                for event in sorted(events, key=lambda x: x['start']):
                    if event['start'] > current_time and current_time < business_end:
                        duration = (event['start'] - current_time).total_seconds() / 3600
                        if duration >= 1:
                            event_start = current_time
                            event_end = current_time + timedelta(hours=1)
                            break
                    current_time = max(current_time, event['end'])
                
                # If no slot found during business hours, try after business hours
                if not event_start and current_time < end_time:
                    if current_time < business_start:
                        current_time = business_start
                    event_start = current_time
                    event_end = current_time + timedelta(hours=1)
            
            if not event_start:
                return {"response": f"{month}月{day}日には空き時間が見つかりませんでした。"}
            
            try:
                event_data = EventCreateRequest(
                    date=f"{year}-{month:02d}-{day:02d}",
                    start_time=event_start.strftime("%H:%M"),
                    end_time=event_end.strftime("%H:%M"),
                    title=title
                )
                
                service = build('calendar', 'v3', credentials=credentials)
                event = await create_calendar_event(service, event_data)
                return {"response": f"{month}月{day}日 {event_start.strftime('%H:%M')}〜{event_end.strftime('%H:%M')}に「{title}」を登録しました"}
            except Exception as e:
                return {"response": f"申し訳ありません。予定の登録ができませんでした：{str(e)}"}
        
        elif event_match:
            month, day, start_hour, end_hour, title = event_match.groups()
            month, day, start_hour, end_hour = map(int, [month, day, start_hour, end_hour])
            
            # Get current year and handle year rollover
            now = datetime.now(pytz.timezone('Asia/Tokyo'))
            year = now.year
            if month < now.month:
                year += 1
                
            try:
                event_data = EventCreateRequest(
                    date=f"{year}-{month:02d}-{day:02d}",
                    start_time=f"{start_hour:02d}:00",
                    end_time=f"{end_hour:02d}:00",
                    title=title
                )
                print(f"\nAttempting to create event: {json.dumps(event_data.dict(), ensure_ascii=False, indent=2)}")
                
                service = build('calendar', 'v3', credentials=credentials)
                event = await create_calendar_event(service, event_data)
                return {"response": f"{month}月{day}日 {start_hour:02d}:00〜{end_hour:02d}:00に「{title}」を登録しました"}
            except Exception as e:
                return {"response": f"申し訳ありません。予定の登録ができませんでした：{str(e)}"}
        
        # Parse date from message for availability check
        date_match = re.search(r'(\d+)月(\d+)日', last_message)
        if not date_match:
            return {"response": "確認したい日付を指定してください。"}
        
        month, day = map(int, date_match.groups())
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)
        
        # Handle year rollover if the requested month is earlier than current month
        year = now.year
        if month < now.month:
            year += 1
            
        target_date = now.replace(year=year, month=month, day=day)
        
        # Set default time range (full day in JST)
        start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Parse time range if specified
        time_range = re.search(r'(\d+)時(?:から|〜|～)(\d+)時', last_message)
        if time_range:
            start_hour, end_hour = map(int, time_range.groups())
            # Ensure end_hour is inclusive (e.g. "17時" means until 17:59:59)
            start_time = target_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            end_time = target_date.replace(hour=end_hour, minute=59, second=59, microsecond=999999)
            print(f"Time range specified: {start_hour}:00-{end_hour}:59")

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
        
        # Events are already in JST from get_calendar_events
        print("\nProcessing events...")
        events.sort(key=lambda x: x['start'])
        
        # Calculate available slots
        print("\nCalculating available slots...")
        available_slots = []
        current_time = start_time
        end_time_jst = end_time
        
        # If specific hours were requested, adjust the time range
        if 'start_hour' in locals() and start_hour is not None:
            current_time = current_time.replace(hour=start_hour, minute=0)
            if 'end_hour' in locals() and end_hour is not None:
                end_time_jst = end_time_jst.replace(hour=end_hour, minute=0)
        
        print(f"Time range (JST): {current_time.strftime('%Y-%m-%d %H:%M')} - {end_time_jst.strftime('%Y-%m-%d %H:%M')}")
        print(f"\nProcessing {len(events)} events in chronological order")
        
        # If no events, return the requested time range
        if not events:
            print("No events found, returning requested time range")
            available_slots = [(current_time, end_time_jst)]
        else:
            # Find available slots between events
            for event in events:
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
            if (end - start).total_seconds() >= 1800:  # 30 minutes = 1800 seconds
                meaningful_slots.append((start, end))
                print(f"Added meaningful slot: {start.strftime('%H:%M')} - {end.strftime('%H:%M')}")
        
        if not meaningful_slots:
            print("No meaningful slots found")
            return {"response": f"{month}月{day}日は空き時間がありません。"}
            
        # Format response in exact Japanese format
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
