import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch, AsyncMock
import sys
import json
from pathlib import Path

JST = ZoneInfo("Asia/Tokyo")

# Add the app directory to the Python path
sys.path.append(str(Path(__file__).parent.parent))

# Create mock redis_config module
class MockRedisClient:
    def __init__(self):
        self.data = {}
    
    def get(self, key):
        return self.data.get(key)
    
    def set(self, key, value, ex=None):
        self.data[key] = value
        return True
    
    def delete(self, *keys):
        for key in keys:
            if key in self.data:
                del self.data[key]
        return True

mock_redis_client = MockRedisClient()

# Create mock module
import types
mock_redis_config = types.ModuleType('redis_config')
setattr(mock_redis_config, 'redis_client', mock_redis_client)
sys.modules['app.redis_config'] = mock_redis_config

from app.chat import parse_datetime_jp, schedule_chat, find_available_slots

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    monkeypatch.setattr('app.chat.redis_client', mock_redis_client)
    monkeypatch.setattr('app.calendar.redis_client', mock_redis_client)
    
    # Mock credentials for schedule_chat
    mock_redis_client.get = Mock(return_value=json.dumps({
        'token': 'fake_token',
        'refresh_token': 'fake_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'fake_client_id',
        'client_secret': 'fake_client_secret',
        'scopes': ['https://www.googleapis.com/auth/calendar.readonly']
    }))

@pytest.fixture
def mock_calendar_service(monkeypatch):
    mock_service = Mock()
    mock_events = Mock()
    mock_service.events.return_value = mock_events
    mock_events.list.return_value = mock_events
    mock_events.insert.return_value = mock_events
    mock_events.execute.return_value = {'items': []}
    
    def mock_build(*args, **kwargs):
        return mock_service
    
    monkeypatch.setattr('app.chat.build', mock_build)
    return mock_service

JST = ZoneInfo("Asia/Tokyo")

def test_parse_datetime_jp_range():
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Test basic time range
    result = parse_datetime_jp("10時〜15時の空いてる時間に会議を入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert start_time.hour == 10
    assert end_time.hour == 15
    assert title == "会議"
    assert is_range == True
    assert intent == 'event_creation'
    
    # Test afternoon range
    result = parse_datetime_jp("午後の空いてる時間に打ち合わせを入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert start_time.hour == 13
    assert end_time.hour == 17
    assert title == "打ち合わせ"
    assert is_range == True
    assert intent == 'event_creation'

def test_find_available_slots():
    now = datetime.now(JST).replace(hour=9, minute=0, second=0, microsecond=0)
    start_time = now
    end_time = now.replace(hour=17)
    
    # Test with no existing events
    events = []
    slots = find_available_slots(events, start_time, end_time)
    assert len(slots) == 1
    assert slots[0][0] == start_time
    assert slots[0][1] == end_time
    
    # Test with one existing event
    events = [{
        'start': {'dateTime': now.replace(hour=12).isoformat()},
        'end': {'dateTime': now.replace(hour=13).isoformat()}
    }]
    slots = find_available_slots(events, start_time, end_time)
    assert len(slots) == 2
    assert (slots[1][1] - slots[1][0]) == timedelta(hours=4)  # Should find 13:00-17:00 slot
    
    # Test with multiple events
    events = [
        {
            'start': {'dateTime': now.replace(hour=10).isoformat()},
            'end': {'dateTime': now.replace(hour=11).isoformat()}
        },
        {
            'start': {'dateTime': now.replace(hour=14).isoformat()},
            'end': {'dateTime': now.replace(hour=15).isoformat()}
        }
    ]
    slots = find_available_slots(events, start_time, end_time)
    assert len(slots) == 3  # Should find 9:00-10:00, 11:00-14:00, and 15:00-17:00 slots

def test_business_hours_validation():
    now = datetime.now(JST).replace(hour=8, minute=0, second=0, microsecond=0)
    
    # Test before business hours
    events = []
    slots = find_available_slots(events, 
                               now.replace(hour=7),
                               now.replace(hour=10))
    assert len(slots) > 0
    assert slots[0][0].hour == 9  # Should start at 9:00
    
    # Test after business hours
    slots = find_available_slots(events,
                               now.replace(hour=16),
                               now.replace(hour=19))
    assert len(slots) > 0
    assert slots[-1][1].hour == 17  # Should end at 17:00

def test_availability_check():
    result = parse_datetime_jp("2月8日の空き時間を教えて")
    assert result is not None
    _, _, _, _, intent = result
    assert intent == 'availability_check'

def test_event_creation():
    result = parse_datetime_jp("2月8日の13時から15時に会議を入れて")
    assert result is not None
    _, _, _, _, intent = result
    assert intent == 'event_creation'

@pytest.mark.asyncio
async def test_schedule_chat(mock_calendar_service):
    with patch('app.chat.get_calendar_service', return_value=mock_calendar_service):
        # Mock calendar service response
        mock_calendar_service.events().list().execute.return_value = {
            'items': []
        }
        mock_calendar_service.events().insert.return_value.execute.return_value = {'id': '123'}
        
        # Test availability check
        response = await schedule_chat({"messages": [{"content": "10時〜15時の空き時間を教えて"}]})
        assert "以下の時間が空いています" in response['response']
        
        # Test event creation
        response = await schedule_chat({"messages": [{"content": "10時〜15時に会議を入れて"}]})
        assert "予定を登録してよろしいですか" in response['response']
        
        # Test when slot is occupied
        mock_calendar_service.events().list().execute.return_value = {
            'items': [{
                'start': {'dateTime': datetime.now(JST).replace(hour=10).isoformat()},
                'end': {'dateTime': datetime.now(JST).replace(hour=15).isoformat()}
            }]
        }
        response = await schedule_chat({"messages": [{"content": "10時〜15時の空き時間を教えて"}]})
        assert "空き時間が見つかりませんでした" in response['response']
