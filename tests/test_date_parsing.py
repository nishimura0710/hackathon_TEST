import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
from unittest.mock import Mock, patch
from pathlib import Path

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

import types
mock_redis_config = types.ModuleType('redis_config')
setattr(mock_redis_config, 'redis_client', mock_redis_client)
sys.modules['app.redis_config'] = mock_redis_config

from app.chat import parse_datetime_jp

JST = ZoneInfo("Asia/Tokyo")

def test_parse_specific_date():
    # Test specific date with time range
    result = parse_datetime_jp("2月8日の13時から15時に会議を入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert intent == 'event_creation'
    
    now = datetime.now(JST)
    expected_year = now.year if now.month <= 2 else now.year + 1
    
    assert start_time.year == expected_year
    assert start_time.month == 2
    assert start_time.day == 8
    assert start_time.hour == 13
    assert end_time.hour == 15
    assert title == "会議"
    assert is_range == True

def test_parse_afternoon_with_date():
    # Test afternoon request with specific date
    result = parse_datetime_jp("2月8日の午後に打ち合わせを入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert intent == 'event_creation'
    
    now = datetime.now(JST)
    expected_year = now.year if now.month <= 2 else now.year + 1
    
    assert start_time.year == expected_year
    assert start_time.month == 2
    assert start_time.day == 8
    assert start_time.hour == 13
    assert end_time.hour == 17
    assert title == "打ち合わせ"
    assert is_range == True

def test_parse_next_day():
    # Test next day request
    today = datetime.now(JST)
    tomorrow = today + timedelta(days=1)
    
    result = parse_datetime_jp(f"{tomorrow.month}月{tomorrow.day}日の10時から12時に会議を入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert intent == 'event_creation'
    
    assert start_time.year == tomorrow.year
    assert start_time.month == tomorrow.month
    assert start_time.day == tomorrow.day
    assert start_time.hour == 10
    assert end_time.hour == 12
    assert title == "会議"
    assert is_range == True

def test_parse_past_date():
    # Test handling of past dates
    now = datetime.now(JST)
    past_date = now - timedelta(days=1)
    
    result = parse_datetime_jp(f"{past_date.month}月{past_date.day}日の14時から16時に会議を入れて")
    assert result is not None
    start_time, end_time, title, is_range, intent = result
    assert intent == 'event_creation'
    
    # Should be scheduled for next year if date is in past
    assert start_time.year == now.year + 1
    assert start_time.month == past_date.month
    assert start_time.day == past_date.day
    assert start_time.hour == 14
    assert end_time.hour == 16
    assert title == "会議"
    assert is_range == True
