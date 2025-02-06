import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
from unittest.mock import Mock, patch
import sys
from pathlib import Path

# Add the app directory to the Python path
sys.path.append(str(Path(__file__).parent.parent))

# Create mock redis_config module
import types
mock_redis_config = types.ModuleType('redis_config')

class MockRedisClient:
    def __init__(self):
        self.data = {}
        self.data['credentials:default_user'] = json.dumps({
            'token': 'fake_token',
            'refresh_token': 'fake_refresh_token',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'client_id': 'fake_client_id',
            'client_secret': 'fake_client_secret',
            'scopes': ['https://www.googleapis.com/auth/calendar.readonly', 'https://www.googleapis.com/auth/calendar.events']
        })
    
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
    
    def exists(self, key):
        return key in self.data

redis_client = MockRedisClient()
setattr(mock_redis_config, 'redis_client', redis_client)
sys.modules['app.redis_config'] = mock_redis_config

from app.chat import schedule_chat

@pytest.fixture(autouse=True)
def setup_redis():
    redis_client.data = {}  # Clear existing data before each test
    redis_client.data['credentials:default_user'] = json.dumps({
        'token': 'fake_token',
        'refresh_token': 'fake_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'fake_client_id',
        'client_secret': 'fake_client_secret',
        'scopes': ['https://www.googleapis.com/auth/calendar.readonly', 'https://www.googleapis.com/auth/calendar.events']
    })
    yield redis_client
    # Clean up after each test
    redis_client.data.clear()

@pytest.fixture
def mock_calendar_service():
    mock_service = Mock()
    mock_events = Mock()
    mock_service.events.return_value = mock_events
    mock_events.list.return_value = mock_events
    mock_events.insert.return_value = mock_events
    mock_events.execute.return_value = {'items': []}
    
    # Configure the mock to handle event creation
    mock_service.events().insert().execute.return_value = {"id": "test_event_id"}
    mock_service.events().list().execute.return_value = {"items": []}
    
    return mock_service

@pytest.fixture(autouse=True)
def mock_get_calendar_service(mock_calendar_service):
    with patch('app.chat.get_calendar_service', return_value=mock_calendar_service) as mock:
        # Configure the mock to handle event creation
        mock_calendar_service.events().insert().execute.return_value = {"id": "test_event_id"}
        mock_calendar_service.events().list().execute.return_value = {"items": []}
        
        # Configure the mock to handle calendar service creation
        mock.return_value = mock_calendar_service
        yield mock

JST = ZoneInfo("Asia/Tokyo")

@pytest.mark.asyncio
async def test_slot_confirmation_yes(setup_redis, mock_calendar_service):
    # Configure mock calendar service
    mock_calendar_service.events.return_value.insert.return_value.execute.return_value = {"id": "test_event_id"}
    mock_calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    
    # Setup test data
    test_slot = {
        "start": "2024-02-08T13:00:00+09:00",
        "end": "2024-02-08T14:00:00+09:00",
        "title": "打ち合わせ",
        "intent": "event_creation"
    }
    setup_redis.set('pending_slot:default_user', json.dumps(test_slot))
    
    response = await schedule_chat({"messages": [{"content": "はい"}]})
    
    assert "を登録しました" in response["response"]
    assert "02月08日" in response["response"]
    assert "13:00" in response["response"]
    assert "14:00" in response["response"]
    assert "打ち合わせ" in response["response"]
    
    # Verify Redis key was deleted
    assert setup_redis.get('pending_slot:default_user') is None

@pytest.mark.asyncio
async def test_slot_confirmation_no(setup_redis, mock_calendar_service):
    # Configure mock calendar service
    mock_calendar_service.events.return_value.insert.return_value.execute.return_value = {"id": "test_event_id"}
    mock_calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    
    # Setup test data
    test_slot = {
        "start": "2024-02-08T13:00:00+09:00",
        "end": "2024-02-08T14:00:00+09:00",
        "title": "打ち合わせ",
        "intent": "event_creation"
    }
    setup_redis.set('pending_slot:default_user', json.dumps(test_slot))
    
    response = await schedule_chat({"messages": [{"content": "いいえ"}]})
    
    assert "キャンセル" in response["response"]
    assert setup_redis.get('pending_slot:default_user') is None
    assert "他の時間帯" in response["response"]
    
    # Verify Redis key was deleted
    assert setup_redis.get('pending_slot:default_user') is None

@pytest.mark.asyncio
async def test_slot_confirmation_without_pending(setup_redis, mock_calendar_service):
    # Configure mock calendar service
    mock_calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    
    response = await schedule_chat({"messages": [{"content": "はい"}]})
    assert response["response"] == "申し訳ありません。日時の指定方法が正しくありません。\n例：2月7日の13時から15時に会議を入れて"

@pytest.mark.asyncio
async def test_slot_confirmation_event_creation_failure(setup_redis, mock_calendar_service):
    # Mock create_calendar_event to return False to simulate failure
    with patch('app.chat.create_calendar_event', return_value=False):
        # Setup test data
        test_slot = {
            "start": "2024-02-08T13:00:00+09:00",
            "end": "2024-02-08T14:00:00+09:00",
            "title": "打ち合わせ",
            "intent": "event_creation"
        }
        setup_redis.set('pending_slot:default_user', json.dumps(test_slot))
        
        response = await schedule_chat({"messages": [{"content": "はい"}]})
        assert response["response"] == "申し訳ありません。予定の登録に失敗しました"

@pytest.mark.asyncio
async def test_multiple_slot_selection(setup_redis, mock_calendar_service):
    # Configure mock calendar service
    mock_calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    mock_calendar_service.events.return_value.insert.return_value.execute.return_value = {"id": "test_event_id"}
    
    # Setup available slots
    available_slots = [
        ["2024-02-08T10:00:00+09:00", "2024-02-08T11:00:00+09:00"],
        ["2024-02-08T14:00:00+09:00", "2024-02-08T15:00:00+09:00"]
    ]
    setup_redis.set('available_slots:default_user', json.dumps(available_slots))
    
    # Test availability check
    response = await schedule_chat({"messages": [{"content": "1番の空き時間を教えて"}]})
    assert "以下の時間が空いています" in response["response"]
    assert "10:00" in response["response"]
    assert "11:00" in response["response"]
    
    # Test event creation
    response = await schedule_chat({"messages": [{"content": "1番で打ち合わせを入れてください"}]})
    assert "02月08日" in response["response"]
    assert "10:00" in response["response"]
    assert "11:00" in response["response"]
    assert "打ち合わせ" in response["response"]
    assert "よろしいですか" in response["response"]
        
    # Verify slot was stored in Redis
    stored_slot = {
        "start": "2024-02-08T10:00:00+09:00",
        "end": "2024-02-08T11:00:00+09:00",
        "title": "打ち合わせ",
        "intent": "event_creation"
    }
    assert json.loads(setup_redis.data['pending_slot:default_user']) == stored_slot

@pytest.mark.asyncio
async def test_invalid_slot_selection(setup_redis, mock_calendar_service):
    # Configure mock calendar service
    mock_calendar_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    mock_calendar_service.events.return_value.insert.return_value.execute.return_value = {"id": "test_event_id"}
    
    # Setup available slots
    available_slots = [
        ["2024-02-08T10:00:00+09:00", "2024-02-08T11:00:00+09:00"]
    ]
    setup_redis.set('available_slots:default_user', json.dumps(available_slots))
    
    # Test invalid slot number for availability check
    response = await schedule_chat({"messages": [{"content": "2番の空き時間を教えて"}]})
    assert "1から1の番号を選択してください" in response["response"]
    
    # Test invalid slot number for event creation
    response = await schedule_chat({"messages": [{"content": "2番で会議を入れてください"}]})
    assert "1から1の番号を選択してください" in response["response"]
