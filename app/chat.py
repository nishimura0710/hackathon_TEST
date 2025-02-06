import os
from fastapi import APIRouter, HTTPException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import re
from .redis_config import redis_client
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import anthropic

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize Anthropic client
anthropic_client = anthropic.Client(api_key=os.getenv("ANTHROPIC_API_KEY"))

async def detect_intent_with_anthropic(text: str) -> tuple[str, str]:
    """Detect intent and extract title using Anthropic's API."""
    try:
        message = anthropic_client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": f"""Analyze this Japanese text and determine if it's asking to:
                1. Check calendar availability ("availability_check")
                2. Create a calendar event ("event_creation")
                Also extract any event title if present.
                
                Text: {text}
                
                Respond in JSON format:
                {{"intent": "availability_check|event_creation", "title": "extracted_title_or_empty"}}"""
            }]
        )
        
        if isinstance(message.content, list) and len(message.content) > 0:
            content = message.content[0]
            if isinstance(content, dict) and "text" in content:
                response = json.loads(content["text"])
                intent = response["intent"]
                title = response.get("title", "")
                # Always use "会議" as default title if none provided
                return intent, title if title else "会議"
        
        logger.error("Invalid response format from Anthropic API")
        return "unknown", "会議"
    except Exception as e:
        logger.error(f"Error detecting intent with Anthropic: {str(e)}")
        return "unknown", "会議"

RESPONSES = {
    'GREETING': 'はい、予定の登録をお手伝いします。いつの予定を登録しますか？',
    'CONFIRM_SLOT': '{start}から{end}で{title}の予定を登録してよろしいですか？（はい/いいえ）',
    'SUCCESS': '{start}から{end}まで{title}を登録しました',
    'NO_SLOTS': '{date}の指定された時間帯に空き時間が見つかりませんでした',
    'NO_SLOTS_AVAILABLE': '申し訳ありませんが、来週の空き時間が見つかりませんでした。',
    'MULTIPLE_SLOTS': '以下の時間帯が空いています：\n{slots}\n\n希望する時間帯の番号を「○番」と入力してください',
    'ERROR': '申し訳ありません。予定の登録に失敗しました',
    'OUTSIDE_HOURS': '申し訳ありません。予定は営業時間内（9:00-17:00）でお願いします',
    'CONFIRM': 'はい、かしこまりました。',
    'INVALID_FORMAT': '申し訳ありません。日時の指定方法が正しくありません。\n例：2月7日の13時から15時に会議を入れて',
    'ALREADY_BOOKED': 'その時間帯は既に予定が入っています。別の時間帯をお選びください。',
    'INVALID_SLOT_NUMBER': '1から{max_slots}の番号を選択してください。',
    'ASK_DURATION': '何時間の予定を入れますか？',
    'SUGGEST_TIME': '{date}の{slots}が空いています。この時間でよろしいですか？（はい/いいえ）'
}

INTENT_PATTERNS = {
    'availability_check': [
        r'空き時間',
        r'空いてる時間',
        r'いつ空いてる',
        r'空いている',
        r'空き状況',
        r'空いてますか',
        r'空いてる[？?]'
    ],
    'event_creation': [
        r'(予定|会議|打ち合わせ).*?(入れて|登録して|予定して)',
        r'(入れて|登録して|予定して).*?(予定|会議|打ち合わせ)',
        r'(予定|会議|打ち合わせ).*?で',
        r'(予定|会議|打ち合わせ).*?に'
    ]
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

async def parse_datetime_jp(text: str) -> tuple[datetime, datetime, str, bool, str] | None:
    logger.info(f"Parsing datetime from text: {text}")
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    target_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Use Anthropic for intent detection and title extraction
    intent, extracted_title = await detect_intent_with_anthropic(text)
    
    # If intent is unknown, default to availability check
    if intent == "unknown":
        intent = "availability_check"
    
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
        
        # Use extracted title from Anthropic if available
        title = extracted_title if extracted_title else "会議"
        
        logger.info(f"Afternoon request: {start_time.strftime('%Y-%m-%d %H:%M')} - {end_time.strftime('%Y-%m-%d %H:%M')}")
        return start_time, end_time, title, True, intent
    
    # Handle simple time range
    time_range_match = re.search(r'(\d{1,2})時[〜～](\d{1,2})時', text)
    if time_range_match:
        start_hour = int(time_range_match.group(1))
        end_hour = int(time_range_match.group(2))
        
        # Create datetime objects with the specified hours
        start_time = target_date.replace(hour=max(9, min(start_hour, 17)))
        end_time = target_date.replace(hour=max(9, min(end_hour, 17)))
        
        # Use extracted title from Anthropic if available
        title = extracted_title if extracted_title else "会議"
        
        return start_time, end_time, title, True, intent
    
    # Handle simple date + availability check pattern
    date_only_match = re.search(r'(\d+)月(\d+)日の?(?:空き時間|空いてる時間)', text)
    if date_only_match:
        month = int(date_only_match.group(1))
        day = int(date_only_match.group(2))
        
        try:
            now = datetime.now(ZoneInfo("Asia/Tokyo"))
            target_date = datetime(now.year, month, day, tzinfo=ZoneInfo("Asia/Tokyo"))
            if target_date < now:
                target_date = datetime(now.year + 1, month, day, tzinfo=ZoneInfo("Asia/Tokyo"))
            start_time = target_date.replace(hour=9, minute=0)
            end_time = target_date.replace(hour=17, minute=0)
            return start_time, end_time, "会議", True, 'availability_check'
        except ValueError:
            logger.error(f"Invalid date: month={month}, day={day}")
            return None
    
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

                # Use extracted title from Anthropic if available
                title = extracted_title if extracted_title else "会議"
                
                # Check if this is a range request (〜 or から)
                is_range = '〜' in text or '～' in text or 'から' in text
                
                return start_time, end_time, title, is_range, intent
            except (ValueError, AttributeError):
                continue
    
    return None

def format_available_slots(slots: list[tuple[datetime, datetime]]) -> str:
    """Format a list of time slots into a numbered list with proper Japanese formatting."""
    formatted = []
    for i, (start, end) in enumerate(slots, 1):
        # Round minutes to nearest hour for cleaner display
        start_rounded = start.replace(minute=0)
        end_rounded = end.replace(minute=0)
        if start.date() == end.date():
            formatted.append(f"{i}. {start_rounded.strftime('%H:%M')}〜{end_rounded.strftime('%H:%M')}")
        else:
            formatted.append(f"{i}. {start_rounded.strftime('%m月%d日(%a) %H:%M')}〜{end_rounded.strftime('%m月%d日(%a) %H:%M')}")
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
        logger.info(f"Processing message: {user_message}")
        
        # Handle confirmation responses (はい/いいえ)
        if user_message in ['はい', 'いいえ']:
            pending_slot_json = redis_client.get('pending_slot:default_user')
            if not pending_slot_json:
                return {"response": RESPONSES['INVALID_FORMAT']}
            
            try:
                pending_slot = json.loads(pending_slot_json)
                if not isinstance(pending_slot, dict):
                    redis_client.delete('pending_slot:default_user')
                    return {"response": RESPONSES['ERROR']}
                
                start_str = pending_slot.get('start')
                end_str = pending_slot.get('end')
                title = pending_slot.get('title', '会議')
                intent = pending_slot.get('intent', 'event_creation')
                
                if not start_str or not end_str:
                    redis_client.delete('pending_slot:default_user')
                    return {"response": RESPONSES['ERROR']}
                
                if user_message == 'いいえ':
                    redis_client.delete('pending_slot:default_user')
                    redis_client.delete('available_slots:default_user')
                    return {"response": "予定の登録をキャンセルしました。他の時間帯をお選びください。"}
                
                try:
                    start = datetime.fromisoformat(start_str)
                    end = datetime.fromisoformat(end_str)
                    
                    if await create_calendar_event(service, start, end, title):
                        redis_client.delete('pending_slot:default_user')
                        redis_client.delete('available_slots:default_user')
                        return {"response": f"{start.strftime('%m月%d日 %H:%M')}から{end.strftime('%H:%M')}まで{title}を登録しました"}
                    
                    redis_client.delete('pending_slot:default_user')
                    return {"response": RESPONSES['ERROR']}
                except Exception as e:
                    logger.error(f"Error creating event: {e}")
                    redis_client.delete('pending_slot:default_user')
                    return {"response": RESPONSES['ERROR']}
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.error(f"Error processing pending slot: {str(e)}")
                redis_client.delete('pending_slot:default_user')
                return {"response": RESPONSES['ERROR']}
        
        # Check for slot selection
        slot_number_match = re.search(r'^(\d+)番', user_message)
        if slot_number_match:
            slot_number = int(slot_number_match.group(1))
            available_slots_json = redis_client.get('available_slots:default_user')
            if not available_slots_json:
                return {"response": RESPONSES['INVALID_FORMAT']}
            
            try:
                available_slots = json.loads(available_slots_json)
                if not isinstance(available_slots, list) or not available_slots:
                    return {"response": RESPONSES['INVALID_FORMAT']}
                
                if not (1 <= slot_number <= len(available_slots)):
                    return {"response": f"1から{len(available_slots)}の番号を選択してください"}
                
                # Validate slot format
                slot = available_slots[slot_number-1]
                if not isinstance(slot, list) or len(slot) != 2:
                    return {"response": RESPONSES['INVALID_FORMAT']}
                start_str, end_str = slot
                if not start_str or not end_str:
                    raise ValueError("Missing start or end time")
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str)
            except (IndexError, ValueError, TypeError) as e:
                logger.error(f"Error processing slot selection: {str(e)}")
                return {"response": f"1から{len(available_slots)}の番号を選択してください"}
            
            # Check if this is an availability check or event creation
            if "空き時間" in user_message or "空いてる時間" in user_message:
                return {"response": f"以下の時間が空いています：\n{format_available_slots([(start, end)])}"} 
            
            # Extract title for event creation
            title = "会議"  # Default title
            title_match = re.search(r'(\d+)番で(.*?)(?:を)?(?:入れて|登録して|予定して)', user_message)
            if title_match:
                extracted = title_match.group(2).strip()
                if extracted and not any(x in extracted for x in ['空いてる', '空き']):
                    title = extracted
            
            # Store the selected slot as pending for event creation
            pending_slot = {
                'start': start.isoformat(),
                'end': end.isoformat(),
                'title': title,
                'intent': 'event_creation'
            }
            redis_client.set('pending_slot:default_user', json.dumps(pending_slot), ex=3600)
            
            return {"response": f"{start.strftime('%m月%d日 %H:%M')}から{end.strftime('%H:%M')}で{title}の予定を登録してよろしいですか？（はい/いいえ）"}
        
        # Try to parse date/time from message
        parsed = await parse_datetime_jp(user_message)
        if parsed:
            start_time, end_time, title, is_range, intent = parsed
            logger.info(f"Successfully parsed datetime: {start_time} - {end_time} for {title} (is_range={is_range})")
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            logger.info(f"Looking for available slots between {start_time} and {end_time}")
            available_slots = find_available_slots(events, start_time, end_time)
            
            if available_slots:
                # Store available slots in Redis
                slots_text = format_available_slots(available_slots)
                redis_client.set(
                    'available_slots:default_user',
                    json.dumps([(s[0].isoformat(), s[1].isoformat()) for s in available_slots]),
                    ex=3600
                )
                
                if intent == 'availability_check':
                    # Only show available slots for availability check
                    return {"response": f"以下の時間が空いています：\n{slots_text}"}
                elif intent == 'event_creation':
                    # For event creation, store the first slot as pending and ask for confirmation
                    slot_start, slot_end = available_slots[0]
                    pending_slot = {
                        'start': slot_start.isoformat(),
                        'end': slot_end.isoformat(),
                        'title': title,
                        'intent': intent
                    }
                    redis_client.set('pending_slot:default_user', json.dumps(pending_slot), ex=3600)
                    return {"response": RESPONSES['CONFIRM_SLOT'].format(
                        start=slot_start.strftime('%m月%d日 %H:%M'),
                        end=slot_end.strftime('%H:%M'),
                        title=title
                    )}
                else:
                    # For unknown intent, just show available slots
                    return {"response": f"以下の時間が空いています：\n{slots_text}"}
            else:
                logger.info("No available slots found in the requested time range")
                return {"response": RESPONSES['NO_SLOTS'].format(
                    date=start_time.strftime('%m月%d日')
                )}
        
        # If no date/time found, return invalid format message
        return {"response": RESPONSES['INVALID_FORMAT']}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing chat request: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="予定の処理に失敗しました"
        )
