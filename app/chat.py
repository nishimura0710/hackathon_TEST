from fastapi import APIRouter, HTTPException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import re
from .redis_config import redis_client
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

async def create_calendar_event(service, start_time: datetime, end_time: datetime, title: str) -> bool:
    try:
        event = {
            'summary': title,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'Asia/Tokyo'
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'Asia/Tokyo'
            },
            'reminders': {
                'useDefault': True
            }
        }
        
        logger.info(f"Creating calendar event: {title} from {start_time.isoformat()} to {end_time.isoformat()}")
        result = service.events().insert(calendarId='primary', body=event).execute()
        logger.info(f"Calendar event created successfully: {result}")
        return True
    except Exception as e:
        logger.error(f"Error creating calendar event: {str(e)}", exc_info=True)
        return False

def parse_datetime_jp(text: str) -> tuple[datetime, datetime, str, bool] | None:
    patterns = [
        r'(\d+)月(\d+)日の(\d+)(?:時|:00)から(\d+)(?:時|:00)',  # 2月7日の13時から15時
        r'(\d+)月(\d+)日(\d+):(\d+)から(\d+):(\d+)',  # 2月7日13:00から15:00
        r'(\d+)月(\d+)日の(\d+)時〜(\d+)時',  # 2月7日の13時〜15時
        r'(\d+)月(\d+)日(\d+)時～(\d+)時'  # 全角チルダ対応
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                month = int(groups[0])
                day = int(groups[1])
                
                if len(groups) == 4:  # First pattern
                    start_hour = int(groups[2])
                    end_hour = int(groups[3])
                    start_minute = 0
                    end_minute = 0
                else:  # Second pattern
                    start_hour = int(groups[2])
                    start_minute = int(groups[3])
                    end_hour = int(groups[4])
                    end_minute = int(groups[5])
                
                now = datetime.now(ZoneInfo("Asia/Tokyo"))
                year = now.year
                
                start_time = datetime(year, month, day, start_hour, start_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                end_time = datetime(year, month, day, end_hour, end_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                
                # If date is in past, assume next year
                if start_time < now:
                    start_time = datetime(year + 1, month, day, start_hour, start_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                    end_time = datetime(year + 1, month, day, end_hour, end_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                
                # Validate business hours (9:00-17:00)
                if start_hour < 9 or start_hour > 17 or end_hour < 9 or end_hour > 17:
                    logger.info(f"Requested time outside business hours: {start_hour}:00-{end_hour}:00")
                    return None

                # Extract event title if present, default to "会議"
                title_match = re.search(r'(.*?)(?:を|で|に)(?:入れて|登録して|予定して)', text)
                title = title_match.group(1) if title_match else "会議"
                
                # Check if this is a range request (〜 or から)
                is_range = '〜' in text or '～' in text or 'から' in text
                
                return start_time, end_time, title, is_range
            except (ValueError, AttributeError):
                continue
    
    return None

def find_longest_available_slot(events: list, start_time: datetime, end_time: datetime) -> tuple[datetime, datetime] | None:
    """Find the longest continuous available time slot between start_time and end_time."""
    if start_time.hour < 9:
        start_time = start_time.replace(hour=9, minute=0)
    if end_time.hour > 17:
        end_time = end_time.replace(hour=17, minute=0)
        
    sorted_events = sorted(events, key=lambda x: datetime.fromisoformat(x['start'].get('dateTime', x['start'].get('date'))))
    
    longest_slot_start = None
    longest_slot_end = None
    longest_duration = timedelta(hours=0)
    
    current = start_time
    for event in sorted_events:
        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
        event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
        
        if current < event_start and event_start > start_time:
            slot_end = min(event_start, end_time)
            duration = slot_end - current
            if duration > longest_duration:
                longest_duration = duration
                longest_slot_start = current
                longest_slot_end = slot_end
        
        current = max(current, event_end)
        if current >= end_time:
            break
    
    # Check final slot after last event
    if current < end_time:
        duration = end_time - current
        if duration > longest_duration:
            longest_slot_start = current
            longest_slot_end = end_time
    
    return (longest_slot_start, longest_slot_end) if longest_slot_start else None

def get_calendar_service():
    try:
        creds_json = redis_client.get('credentials:default_user')
        logger.info(f"Checking credentials in Redis: {'Found' if creds_json else 'Not found'}")
        
        if not creds_json:
            logger.error("No credentials found in Redis")
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )
        
        try:
            creds_dict = json.loads(creds_json)
            logger.info("Successfully parsed credentials from Redis")
            
            # Log credential details (excluding sensitive info)
            logger.info(f"Credential scopes: {creds_dict.get('scopes', [])}")
            logger.info(f"Token expiry status: {'token' in creds_dict}")
            
            credentials = Credentials(**creds_dict)
            logger.info("Successfully created Credentials object")
            
            service = build('calendar', 'v3', credentials=credentials)
            logger.info("Successfully built calendar service")
            
            # Test calendar access
            try:
                test_result = service.calendarList().list().execute()
                logger.info(f"Calendar access test successful. Found {len(test_result.get('items', []))} calendars")
            except Exception as e:
                logger.error(f"Calendar access test failed: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=401,
                    detail="カレンダーへのアクセスに失敗しました。再度ログインしてください。"
                )
            
            return service
        except json.JSONDecodeError as e:
            logger.error(f"Invalid credentials format in Redis: {str(e)}")
            raise HTTPException(
                status_code=401,
                detail="カレンダーの認証が必要です"
            )
        except Exception as e:
            logger.error(f"Error creating calendar service: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="カレンダーサービスの初期化に失敗しました"
            )
    except Exception as e:
        logger.error(f"Unexpected error in get_calendar_service: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="カレンダーサービスの初期化に失敗しました"
        )

@router.post("/schedule")
async def schedule_chat(message: dict):
    try:
        service = get_calendar_service()
        user_message = message.get('messages', [{}])[-1].get('content', '')
        
        # Try to parse date/time from message
        parsed = parse_datetime_jp(user_message)
        if parsed:
            start_time, end_time, title, is_range = parsed
            logger.info(f"Successfully parsed datetime: {start_time} - {end_time} for {title} (is_range={is_range})")
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            if is_range:
                logger.info(f"Looking for longest available slot between {start_time} and {end_time}")
                slot = find_longest_available_slot(events, start_time, end_time)
                if slot:
                    slot_start, slot_end = slot
                    logger.info(f"Found longest available slot: {slot_start} to {slot_end}")
                    if await create_calendar_event(service, slot_start, slot_end, title):
                        return {
                            "response": f"{slot_start.strftime('%m月%d日 %H:%M')}から{slot_end.strftime('%H:%M')}まで{title}を登録しました"
                        }
                    else:
                        return {"response": "予定の登録に失敗しました。もう一度お試しください。"}
                else:
                    logger.info("No available slots found in the requested time range")
                    # Find all available slots in the time range
                    available_slots = []
                    current = start_time
                    for event in events:
                        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                        if current < event_start:
                            available_slots.append((current, event_start))
                        current = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                    
                    if current < end_time:
                        available_slots.append((current, end_time))
                    
                    response = f"{start_time.strftime('%m月%d日')}の{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}の間に空き時間が見つかりませんでした。\n"
                    if available_slots:
                        response += "以下の時間が空いています：\n"
                        for start, end in available_slots:
                            if start.date() == end.date():
                                response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%H:%M')}\n"
                            else:
                                response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%m月%d日(%a) %H:%M')}\n"
                    return {"response": response}
            else:
                logger.info("Not a range request, proceeding with standard slot finding")
        else:
            logger.info("Could not parse datetime or time is outside business hours")
            
            # Extract date from message if possible
            date_match = re.search(r'(\d+)月(\d+)日', user_message)
            afternoon_request = '午後' in user_message
            
            now = datetime.now(ZoneInfo("Asia/Tokyo"))
            next_week = now + timedelta(days=7)
            
            if date_match:
                month = int(date_match.group(1))
                day = int(date_match.group(2))
                target_date = datetime(now.year, month, day, tzinfo=ZoneInfo("Asia/Tokyo"))
                if target_date < now:
                    target_date = datetime(now.year + 1, month, day, tzinfo=ZoneInfo("Asia/Tokyo"))
                
                if afternoon_request:
                    start_time = target_date.replace(hour=13, minute=0)
                    end_time = target_date.replace(hour=17, minute=0)
                else:
                    start_time = target_date.replace(hour=9, minute=0)
                    end_time = target_date.replace(hour=17, minute=0)
            else:
                start_time = now
                end_time = next_week
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            logger.info(f"Found {len(events)} events between {start_time} and {end_time}")
            
            # Find first available 1-hour slot
            current = start_time.replace(minute=0)
            if afternoon_request:
                # For afternoon requests, start from 13:00
                current = current.replace(hour=13) if current.hour < 13 else current
            else:
                # For other requests, start from 9:00
                current = current.replace(hour=9) if current.hour < 9 else current
            
            while current < end_time and current.hour < 17:
                slot_end = current + timedelta(hours=1)
                if slot_end > end_time:
                    break
                    
                has_conflict = False
                for event in events:
                    event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                    event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                    
                    if (current < event_end and slot_end > event_start):
                        has_conflict = True
                        current = event_end
                        if current.minute > 0:  # Round up to next hour
                            current = (current + timedelta(hours=1)).replace(minute=0)
                        break
                
                if not has_conflict:
                    # Found an available slot
                    title = "会議"  # Default title
                    title_match = re.search(r'(.*?)(?:を|で|に)(?:入れて|登録して|予定して)', user_message)
                    if title_match:
                        title = title_match.group(1)
                    
                    if await create_calendar_event(service, current, slot_end, title):
                        return {
                            "response": f"{current.strftime('%m月%d日 %H:%M')}から{slot_end.strftime('%H:%M')}まで{title}を登録しました"
                        }
                    else:
                        return {"response": "予定の登録に失敗しました。もう一度お試しください。"}
                
                current += timedelta(hours=1)
            
            # Find first available slot
            current_time = start_time.replace(minute=0)
            if current_time.hour < 9:
                current_time = current_time.replace(hour=9)
            if afternoon_request and current_time.hour < 13:
                current_time = current_time.replace(hour=13)
            
            logger.info(f"Looking for slots starting at {current_time} (afternoon_request={afternoon_request})")
            sorted_events = sorted(events, key=lambda x: datetime.fromisoformat(x['start'].get('dateTime', x['start'].get('date'))))
            logger.info(f"Found {len(sorted_events)} events for the day")
            
            # Initialize variables for slot finding
            available_slots = []
            
            # Check each time slot until we find one that's available
            while current_time.hour < 17 and current_time.date() == start_time.date():
                slot_end = current_time + timedelta(hours=1)
                logger.info(f"Checking slot: {current_time} to {slot_end}")
                
                # Skip if we're before business hours or before afternoon for afternoon requests
                if current_time.hour < 9 or (afternoon_request and current_time.hour < 13):
                    current_time = current_time.replace(hour=13 if afternoon_request else 9)
                    logger.info(f"Adjusted time to {current_time} due to business hours/afternoon request")
                    continue
                
                # Check if this slot conflicts with any event
                slot_available = True
                for event in sorted_events:
                    event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                    event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                    
                    # Check if the event overlaps with our current slot
                    if (current_time < event_end and slot_end > event_start and
                        event_start.date() == current_time.date()):
                        slot_available = False
                        logger.info(f"Slot conflicts with event: {event.get('summary')} ({event_start} to {event_end})")
                        current_time = event_end
                        if current_time.minute > 0:
                            current_time = (current_time + timedelta(hours=1)).replace(minute=0)
                        break
                
                if slot_available and current_time.hour < 17:
                    logger.info(f"Found available slot: {current_time} to {slot_end}")
                    available_slots.append((current_time, slot_end))
                    
                    # If this is a suitable slot for our request, create the event
                    if not afternoon_request or (afternoon_request and current_time.hour >= 13):
                        title = "会議"
                        title_match = re.search(r'(.*?)(?:を|で|に)(?:入れて|登録して|予定して)', user_message)
                        if title_match:
                            title = title_match.group(1).strip()
                        
                        try:
                            logger.info(f"Attempting to create event '{title}' at {current_time} to {slot_end}")
                            if await create_calendar_event(service, current_time, slot_end, title):
                                logger.info("Event creation successful")
                                return {
                                    "response": f"{current_time.strftime('%m月%d日 %H:%M')}から{slot_end.strftime('%H:%M')}まで{title}を登録しました"
                                }
                            else:
                                logger.error("Event creation failed")
                                return {"response": "予定の登録に失敗しました。もう一度お試しください。"}
                        except Exception as e:
                            logger.error(f"Error creating event: {str(e)}", exc_info=True)
                            return {"response": "予定の登録に失敗しました。もう一度お試しください。"}
                        
                current_time += timedelta(hours=1)
                
            # If we get here, we didn't find a suitable slot or failed to create the event
            if available_slots:
                # Format available slots with weekday names
                available_slots_text = "以下の時間が空いています：\n" + "\n".join([
                    f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%H:%M')}"
                    for start, end in available_slots
                ])
                return {"response": available_slots_text}
            
            # If no slot was found, show available slots
            response = "指定された日時に空き時間が見つかりませんでした。以下の時間が空いています：\n"
            current_time = start_time.replace(minute=0)
            if current_time.hour < 9:
                current_time = current_time.replace(hour=9)
            
            # Reset and collect all available slots for display
            available_slots = []
            for event in sorted_events:
                event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                
                if current_time + timedelta(hours=1) <= event_start and current_time.hour < 17:
                    slot_end = min(event_start, current_time.replace(hour=17))
                    if afternoon_request:
                        if current_time.hour >= 13 and current_time + timedelta(hours=1) <= slot_end:
                            available_slots.append((current_time, slot_end))
                    else:
                        available_slots.append((current_time, slot_end))
                
                current_time = max(current_time, event_end)
                if current_time.minute > 0:
                    current_time = (current_time + timedelta(hours=1)).replace(minute=0)
            
            # Add remaining time after last event
            if current_time.hour < 17:
                slot_end = current_time.replace(hour=17)
                if afternoon_request:
                    if current_time.hour >= 13 and current_time + timedelta(hours=1) <= slot_end:
                        available_slots.append((current_time, slot_end))
                else:
                    available_slots.append((current_time, slot_end))
            
            # Format response with available slots
            for start, end in available_slots:
                if start.date() == end.date():
                    response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%H:%M')}\n"
                else:
                    response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%m月%d日(%a) %H:%M')}\n"
            return {"response": response}
        
        # If no date/time found, return available slots
        now = datetime.now(ZoneInfo("Asia/Tokyo"))
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=7)).isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        free_slots = []
        current_time = now.replace(minute=0, second=0, microsecond=0)  # Round to hour
        business_start = current_time.replace(hour=9)  # 9 AM
        business_end = current_time.replace(hour=17)  # 5 PM
        
        for i in range(7):  # Next 7 days
            day_start = business_start + timedelta(days=i)
            day_end = business_end + timedelta(days=i)
            
            day_events = [
                event for event in events
                if datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date'))).date() == day_start.date()
            ]
            
            current = day_start
            for event in sorted(day_events, key=lambda x: datetime.fromisoformat(x['start'].get('dateTime', x['start'].get('date')))):
                event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                
                if current < event_start and (event_start - current) >= timedelta(hours=1):
                    free_slots.append({
                        "start": current.isoformat(),
                        "end": event_start.isoformat()
                    })
                current = max(current, event_end)
            
            if current < day_end and (day_end - current) >= timedelta(hours=1):
                free_slots.append({
                    "start": current.isoformat(),
                    "end": day_end.isoformat()
                })
        
        if not free_slots:
            response = "申し訳ありませんが、来週の空き時間が見つかりませんでした。"
        else:
            response = "以下の時間が空いています：\n"
            for slot in free_slots:
                start = datetime.fromisoformat(slot['start'])
                end = datetime.fromisoformat(slot['end'])
                if start.date() == end.date():
                    response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%H:%M')}\n"
                else:
                    response += f"- {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%m月%d日(%a) %H:%M')}\n"
        
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing chat request: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予定の処理に失敗しました"
        )
