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

RESPONSES = {
    'GREETING': 'はい、予定の登録をお手伝いします。いつの予定を登録しますか？',
    'CONFIRM_SLOT': '{start}から{end}で{title}の予定を登録してよろしいですか？',
    'SUCCESS': '{start}から{end}まで{title}を登録しました',
    'NO_SLOTS': '{date}の指定された時間帯に空き時間が見つかりませんでした',
    'MULTIPLE_SLOTS': '以下の時間帯が空いています：\n{slots}\nご希望の時間帯を教えてください',
    'ERROR': '申し訳ありません。予定の登録に失敗しました',
    'OUTSIDE_HOURS': '申し訳ありません。予定は営業時間内（9:00-17:00）でお願いします',
    'CONFIRM': 'はい、かしこまりました。',
    'INVALID_FORMAT': '申し訳ありません。日時の指定方法が正しくありません。\n例：2月7日の13時から15時に会議を入れて',
    'ALREADY_BOOKED': 'その時間帯は既に予定が入っています。別の時間帯をお選びください。',
    'ASK_DURATION': '何時間の予定を入れますか？',
    'SUGGEST_TIME': '{date}の{slots}が空いています。この時間でよろしいですか？'
}

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
    logger.info(f"Parsing datetime from text: {text}")
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    target_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # First try to extract any date information
    date_match = re.search(r'(\d+)月(\d+)日', text)
    if date_match:
        month = int(date_match.group(1))
        day = int(date_match.group(2))
        year = now.year
        
        try:
            target_date = datetime(year, month, day, 
                                 tzinfo=ZoneInfo("Asia/Tokyo"))
            # If date is in past, assume next year
            if target_date < now:
                target_date = datetime(year + 1, month, day, 
                                     tzinfo=ZoneInfo("Asia/Tokyo"))
            logger.info(f"Found date: {target_date.strftime('%Y-%m-%d')}")
        except ValueError:
            logger.error(f"Invalid date: month={month}, day={day}")
            return None
    
    # Handle afternoon range
    afternoon_match = re.search(r'午後の?', text)
    if afternoon_match:
        start_time = target_date.replace(hour=13)  # 13:00
        end_time = target_date.replace(hour=17)    # 17:00
        
        # Extract title
        title = "会議"
        # Remove date, time, and context words before extracting title
        cleaned_text = re.sub(r'\d+月\d+日|午後の?|空いてる時間に?|の|に|のに', '', text)
        title_match = re.search(r'(\S+?)を(?:入れて|登録して|予定して)', cleaned_text)
        if title_match:
            extracted = title_match.group(1).strip()
            if extracted and not any(x in extracted for x in ['空いてる', '空き']):
                title = extracted
        
        logger.info(f"Afternoon request: {start_time.strftime('%Y-%m-%d %H:%M')} - {end_time.strftime('%Y-%m-%d %H:%M')}")
        return start_time, end_time, title, True
    
    # Handle simple time range
    time_range_match = re.search(r'(\d{1,2})時[〜～](\d{1,2})時', text)
    if time_range_match:
        start_hour = int(time_range_match.group(1))
        end_hour = int(time_range_match.group(2))
        
        # Create datetime objects with the specified hours
        start_time = target_date.replace(hour=max(9, min(start_hour, 17)))
        end_time = target_date.replace(hour=max(9, min(end_hour, 17)))
        
        # Extract title
        title = "会議"
        # First clean up the text by removing date/time patterns
        cleaned_text = re.sub(r'\d+月\d+日の午後に|\d+月\d+日に|の午後に|午後に|の午後|午後', '', text)
        
        # Then extract the core title using a more precise pattern
        title_match = re.search(r'([^を\s]+?)を(?:入れて|登録して|予定して)', cleaned_text)
        if title_match:
            extracted = title_match.group(1).strip()
            # Only use the extracted title if it's not a time-related word and not empty
            if extracted and not any(x in extracted for x in ['空いてる', '空き']):
                # Remove any remaining particles by splitting on particles and taking the last non-particle part
                # First remove any leading particles
                extracted = re.sub(r'^(の|に|のに)', '', extracted)
                # Then remove any trailing particles
                extracted = re.sub(r'(の|に|のに)$', '', extracted)
                # Finally, if there are still particles in the middle, split and take the last meaningful part
                if re.search(r'(の|に|のに)', extracted):
                    parts = re.split(r'(の|に|のに)', extracted)
                    meaningful_parts = [p.strip() for p in parts if p.strip() and p not in ['の', 'に', 'のに']]
                    if meaningful_parts:
                        extracted = meaningful_parts[-1]
                title = extracted.strip()
        
        return start_time, end_time, title, True
    
    # Handle patterns with dates
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
                # Use target_date's year which already handles past dates
                start_time = datetime(target_date.year, month, day, start_hour, start_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                end_time = datetime(target_date.year, month, day, end_hour, end_minute, tzinfo=ZoneInfo("Asia/Tokyo"))
                
                logger.info(f"Explicit time range: {start_time.strftime('%Y-%m-%d %H:%M')} - {end_time.strftime('%Y-%m-%d %H:%M')}")
                
                # Validate business hours (9:00-17:00)
                if start_hour < 9 or start_hour > 17 or end_hour < 9 or end_hour > 17:
                    logger.info(f"Requested time outside business hours: {start_hour}:00-{end_hour}:00")
                    return None

                # Extract event title if present, default to "会議"
                title = "会議"
                # First remove all date/time/context patterns
                cleaned_text = re.sub(r'\d+月\d+日|\d+時(?:から|〜|～)\d+時(?:まで)?|午後の?|空いてる時間に?|の|に|で|から|まで', '', text)
                # Then extract title
                title_match = re.search(r'(\S+?)(?:を)?(?:入れて|登録して|予定して)', cleaned_text)
                if title_match and title_match.group(1):
                    extracted = title_match.group(1).strip()
                    if extracted and not any(x in extracted for x in ['空いてる', '空き']):
                        title = extracted
                
                # Check if this is a range request (〜 or から)
                is_range = '〜' in text or '～' in text or 'から' in text
                
                return start_time, end_time, title, is_range
            except (ValueError, AttributeError):
                continue
    
    return None

def format_available_slots(slots: list[tuple[datetime, datetime]]) -> str:
    """Format a list of time slots into a numbered list with proper Japanese formatting."""
    formatted = []
    for i, (start, end) in enumerate(slots, 1):
        if start.date() == end.date():
            formatted.append(f"{i}. {start.strftime('%H:%M')}〜{end.strftime('%H:%M')}")
        else:
            formatted.append(f"{i}. {start.strftime('%m月%d日(%a) %H:%M')}〜{end.strftime('%m月%d日(%a) %H:%M')}")
    return '\n'.join(formatted)

def find_available_slots(events: list, start_time: datetime, end_time: datetime, 
                        min_duration: timedelta = timedelta(hours=1)) -> list[tuple[datetime, datetime]]:
    """Find all available time slots between start_time and end_time with minimum duration."""
    if start_time.hour < 9:
        start_time = start_time.replace(hour=9, minute=0)
    if end_time.hour > 17:
        end_time = end_time.replace(hour=17, minute=0)
    
    if start_time >= end_time:
        return []
    
    available_slots = []
    current = start_time
    sorted_events = sorted(events, key=lambda x: datetime.fromisoformat(x['start'].get('dateTime', x['start'].get('date'))))
    
    for event in sorted_events:
        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
        event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
        
        if event_end <= start_time:
            continue
        if event_start >= end_time:
            break
            
        if current < event_start:
            duration = event_start - current
            if duration >= min_duration:
                available_slots.append((current, event_start))
        current = max(current, event_end)
    
    if current < end_time and (end_time - current) >= min_duration:
        available_slots.append((current, end_time))
    
    return available_slots

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
        
        # Check for confirmation of suggested slot
        if '確認' in user_message or 'はい' in user_message:
            pending_slot = json.loads(redis_client.get('pending_slot:default_user') or '{}')
            if pending_slot:
                if await create_calendar_event(service, 
                    datetime.fromisoformat(pending_slot['start']),
                    datetime.fromisoformat(pending_slot['end']),
                    pending_slot['title']):
                    redis_client.delete('pending_slot:default_user')
                    return {"response": RESPONSES['SUCCESS'].format(
                        start=datetime.fromisoformat(pending_slot['start']).strftime('%m月%d日 %H:%M'),
                        end=datetime.fromisoformat(pending_slot['end']).strftime('%H:%M'),
                        title=pending_slot['title']
                    )}
                return {"response": RESPONSES['ERROR']}
            return {"response": RESPONSES['GREETING']}
        
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
                if slot and (slot[1] - slot[0]) >= timedelta(hours=1):
                    slot_start, slot_end = slot
                    logger.info(f"Found longest available slot: {slot_start} to {slot_end}")
                    
                    # Check if the slot overlaps with any events
                    has_conflict = False
                    for event in events:
                        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                        event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                        if (slot_start < event_end and slot_end > event_start):
                            has_conflict = True
                            break
                    
                    if not has_conflict:
                        if await create_calendar_event(service, slot_start, slot_end, title):
                            return {
                                "response": f"{slot_start.strftime('%m月%d日 %H:%M')}から{slot_end.strftime('%H:%M')}まで{title}を登録しました"
                            }
                        else:
                            return {"response": "予定の登録に失敗しました。もう一度お試しください。"}
                    else:
                        logger.info("Found slot has conflicts with existing events")
                        return {"response": f"{start_time.strftime('%m月%d日')}の{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}の間に空き時間が見つかりませんでした。"}
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
                    
                    # Find all available slots
                    available_slots = find_available_slots(events, start_time, end_time)
                    if available_slots:
                        slots_text = format_available_slots(available_slots)
                        redis_client.set(
                            'available_slots:default_user',
                            json.dumps([(s[0].isoformat(), s[1].isoformat()) for s in available_slots]),
                            ex=3600
                        )
                        return {"response": RESPONSES['MULTIPLE_SLOTS'].format(
                            slots=slots_text
                        )}
                    
                    return {"response": RESPONSES['NO_SLOTS'].format(
                        date=start_time.strftime('%m月%d日')
                    )}
            else:
                logger.info("Not a range request, proceeding with standard slot finding")
        else:
            # Check for slot selection
            slot_number_match = re.search(r'^(\d+)番', user_message)
            if slot_number_match:
                slot_number = int(slot_number_match.group(1))
                available_slots = json.loads(redis_client.get('available_slots:default_user') or '[]')
                if available_slots and 1 <= slot_number <= len(available_slots):
                    start, end = [datetime.fromisoformat(t) for t in available_slots[slot_number-1]]
                    
                    # Store the selected slot as pending
                    pending_slot = {
                        'start': start.isoformat(),
                        'end': end.isoformat(),
                        'title': '会議'  # Default title
                    }
                    redis_client.set('pending_slot:default_user', json.dumps(pending_slot), ex=3600)
                    
                    return {"response": RESPONSES['CONFIRM_SLOT'].format(
                        start=start.strftime('%H:%M'),
                        end=end.strftime('%H:%M'),
                        title=pending_slot['title']
                    )}
                return {"response": RESPONSES['INVALID_FORMAT']}
            
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
            
            # Find available slots considering afternoon request
            start_hour = 13 if afternoon_request else 9
            start_time = start_time.replace(hour=start_hour, minute=0)
            end_time = start_time.replace(hour=17, minute=0)
            
            logger.info(f"Looking for slots between {start_time} and {end_time}")
            available_slots = find_available_slots(events, start_time, end_time)
            
            if available_slots:
                # Store available slots in Redis
                slots_text = format_available_slots(available_slots)
                redis_client.set(
                    'available_slots:default_user',
                    json.dumps([(s[0].isoformat(), s[1].isoformat()) for s in available_slots]),
                    ex=3600
                )
                
                # Take the first available slot and create the event
                start_time, end_time = available_slots[0]
                title = "会議"
                title_match = re.search(r'(.*?)(?:を|で|に)(?:入れて|登録して|予定して)', user_message)
                if title_match:
                    title = title_match.group(1).strip()
                
                try:
                    logger.info(f"Attempting to create event '{title}' at {start_time} to {end_time}")
                    if await create_calendar_event(service, start_time, end_time, title):
                        logger.info("Event creation successful")
                        return {"response": RESPONSES['SUCCESS'].format(
                            start=start_time.strftime('%m月%d日 %H:%M'),
                            end=end_time.strftime('%H:%M'),
                            title=title
                        )}
                    else:
                        logger.error("Event creation failed")
                        return {"response": RESPONSES['ERROR']}
                except Exception as e:
                    logger.error(f"Error creating event: {str(e)}", exc_info=True)
                    return {"response": RESPONSES['ERROR']}
                
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
        
        # If no date/time found, return available slots for the next 7 days
        now = datetime.now(ZoneInfo("Asia/Tokyo"))
        week_end = now + timedelta(days=7)
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=week_end.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        all_available_slots = []
        
        # Check each day
        for i in range(7):
            day_start = (now + timedelta(days=i)).replace(hour=9, minute=0, second=0, microsecond=0)
            day_end = day_start.replace(hour=17)
            
            # Get available slots for this day
            day_slots = find_available_slots(events, day_start, day_end)
            all_available_slots.extend(day_slots)
        
        if not all_available_slots:
            return {"response": "申し訳ありませんが、来週の空き時間が見つかりませんでした。"}
        
        # Store all slots in Redis
        redis_client.set(
            'available_slots:default_user',
            json.dumps([(s[0].isoformat(), s[1].isoformat()) for s in all_available_slots]),
            ex=3600
        )
        
        # Format and return the response
        slots_text = format_available_slots(all_available_slots)
        return {"response": RESPONSES['MULTIPLE_SLOTS'].format(slots=slots_text)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing chat request: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予定の処理に失敗しました"
        )
