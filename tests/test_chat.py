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
    def get(self, key):
        return None
    def set(self, key, value, ex=None):
        pass

mock_redis_client = MockRedisClient()

# Create mock module
import types
mock_redis_config = types.ModuleType('redis_config')
mock_redis_config.redis_client = mock_redis_client
sys.modules['app.redis_config'] = mock_redis_config

from app.chat import parse_datetime_jp, find_longest_available_slot, schedule_chat

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
    start_time, end_time, title, is_range = result
    assert start_time.hour == 10
    assert end_time.hour == 15
    assert title == "会議"
    assert is_range == True
    
    # Test afternoon range
    result = parse_datetime_jp("午後の空いてる時間に打ち合わせを入れて")
    assert result is not None
    start_time, end_time, title, is_range = result
    assert start_time.hour == 13
    assert end_time.hour == 17
    assert title == "打ち合わせ"
    assert is_range == True

def test_find_longest_available_slot():
    now = datetime.now(JST).replace(hour=9, minute=0, second=0, microsecond=0)
    start_time = now
    end_time = now.replace(hour=17)
    
    # Test with no existing events
    events = []
    slot = find_longest_available_slot(events, start_time, end_time)
    assert slot is not None
    assert slot[0] == start_time
    assert slot[1] == end_time
    
    # Test with one existing event
    events = [{
        'start': {'dateTime': now.replace(hour=12).isoformat()},
        'end': {'dateTime': now.replace(hour=13).isoformat()}
    }]
    slot = find_longest_available_slot(events, start_time, end_time)
    assert slot is not None
    assert (slot[1] - slot[0]) == timedelta(hours=4)  # Should find 13:00-17:00 slot
    
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
    slot = find_longest_available_slot(events, start_time, end_time)
    assert slot is not None
    assert (slot[1] - slot[0]) == timedelta(hours=3)  # Should find 11:00-14:00 slot

def test_business_hours_validation():
    now = datetime.now(JST).replace(hour=8, minute=0, second=0, microsecond=0)
    
    # Test before business hours
    events = []
    slot = find_longest_available_slot(events, 
                                     now.replace(hour=7),
                                     now.replace(hour=10))
    assert slot is not None
    assert slot[0].hour == 9  # Should start at 9:00
    
    # Test after business hours
    slot = find_longest_available_slot(events,
                                     now.replace(hour=16),
                                     now.replace(hour=19))
    assert slot is not None
    assert slot[1].hour == 17  # Should end at 17:00

@pytest.mark.asyncio
async def test_schedule_chat(mock_calendar_service):
    with patch('app.chat.get_calendar_service', return_value=mock_calendar_service):
        # Mock calendar service response
        mock_calendar_service.events().list().execute.return_value = {
            'items': []
        }
        mock_calendar_service.events().insert.return_value.execute.return_value = {'id': '123'}
        
        # Test scheduling in available slot
        response = await schedule_chat({"messages": [{"content": "10時〜15時の空いてる時間に会議を入れて"}]})
        assert "会議を登録しました" in response['response']
        
        # Test when slot is occupied
        mock_calendar_service.events().list().execute.return_value = {
            'items': [{
                'start': {'dateTime': datetime.now(JST).replace(hour=10).isoformat()},
                'end': {'dateTime': datetime.now(JST).replace(hour=15).isoformat()}
            }]
        }
        response = await schedule_chat({"messages": [{"content": "10時〜15時の空いてる時間に会議を入れて"}]})
        assert "空き時間が見つかりませんでした" in response['response']
