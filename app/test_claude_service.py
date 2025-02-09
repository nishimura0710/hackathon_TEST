import pytest
from datetime import datetime, timedelta
import json
from claude_service import ClaudeService
from unittest.mock import patch

class MockContent:
    def __init__(self, text):
        self.text = text

class MockResponse:
    def __init__(self, text):
        self.content = [MockContent(text)]

class MockMessages:
    def create(self, model=None, max_tokens=None, messages=None, **kwargs):
        """Simple mock implementation for testing."""
        try:
            # Extract calendar data and time range
            prompt = messages[0].get('content', '')
            busy_slots = []
            
            if 'Busyデータ: ' in prompt:
                try:
                    json_str = prompt.split('Busyデータ: ')[1].split('\n')[0]
                    calendar_data = json.loads(json_str)
                    busy_slots = calendar_data["calendars"]["test@example.com"]["busy"]
                except (IndexError, KeyError, json.JSONDecodeError):
                    pass
            
            # Extract time range from prompt
            prompt = messages[0].get('content', '')
            
            # Default time range based on prompt content
            if "午前" in prompt:
                start_time = datetime(2025, 2, 12, 9, 0)
                end_time = datetime(2025, 2, 12, 12, 0)
                slot_start = start_time
                reason = "午前中の空き時間帯を選択しました"
            else:
                start_time = datetime(2025, 2, 12, 13, 0)
                end_time = datetime(2025, 2, 12, 18, 0)
                slot_start = start_time
                reason = "午後の空き時間帯を選択しました"
            
            # Try to extract actual time range if provided
            try:
                if '指定された範囲: ' in prompt:
                    time_range = prompt.split('指定された範囲: ')[1].split('\n')[0]
                    start_str, end_str = time_range.split(' 〜 ')
                    start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                    end_time = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
                    slot_start = start_time
            except (IndexError, ValueError):
                pass  # Use default time range
            
            # Ensure slot is within business hours (9:00-18:00)
            if slot_start.hour < 9:
                slot_start = slot_start.replace(hour=9, minute=0)
            elif slot_start.hour >= 18:
                return None
            
            # Set end time exactly 1 hour after start
            slot_end = slot_start + timedelta(hours=1)
            
            # Validate end time is within bounds
            if slot_end > end_time or slot_end.hour > 18:
                return None
            
            # Check for conflicts with busy slots
            buffer = timedelta(minutes=15)
            has_conflict = True
            while has_conflict and slot_end.hour <= 18:
                has_conflict = False
                for busy in busy_slots:
                    busy_start = datetime.fromisoformat(busy["start"]).replace(tzinfo=None)
                    busy_end = datetime.fromisoformat(busy["end"]).replace(tzinfo=None)
                    
                    # Check for overlap including buffer time
                    if (slot_start < busy_end + buffer and slot_end + buffer > busy_start):
                        has_conflict = True
                        slot_start = busy_end + buffer
                        slot_end = slot_start + timedelta(hours=1)
                        break
                
                # If we've gone past business hours, return None
                if slot_end.hour > 18:
                    return None
            
            # Create response with proper JSON formatting
            response = {
                "suggested_time": {
                    "start": slot_start.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                    "end": slot_end.strftime("%Y-%m-%dT%H:%M:%S+09:00")
                },
                "reason": reason
            }
            
            # Return response in Claude's format
            text = f"""分析の結果、以下の時間枠が最適です：

{json.dumps(response, ensure_ascii=False)}

この時間で予定を登録します。"""
            return MockResponse(text)
            
        except Exception as e:
            print(f"Mock error: {str(e)}")
            return None

class MockAnthropicClient:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.messages = MockMessages()

def test_japanese_time_expressions():
    """Test handling of Japanese time expressions and timezone."""
    with patch('os.getenv') as mock_getenv, \
         patch('claude_service.Anthropic') as mock_anthropic:
        mock_getenv.return_value = "test-api-key"
        mock_client = MockAnthropicClient(api_key="test-api-key")
        mock_anthropic.return_value = mock_client
        service = ClaudeService()
        
        # Test morning slot (午前中)
        start_time = datetime(2025, 2, 12, 9, 0)
        end_time = datetime(2025, 2, 12, 12, 0)
        result = service.analyze_free_slots([], start_time, end_time, "test@example.com")
        
        assert result is not None
        assert "suggested_time" in result
        slot_start = datetime.fromisoformat(result["suggested_time"]["start"]).replace(tzinfo=None)
        slot_end = datetime.fromisoformat(result["suggested_time"]["end"]).replace(tzinfo=None)
        assert "+09:00" in result["suggested_time"]["start"]  # JST timezone
        assert 9 <= slot_start.hour < 12  # Morning hours
        assert slot_end - slot_start == timedelta(hours=1)  # 1-hour duration
        
        # Test afternoon slot (午後)
        start_time = datetime(2025, 2, 12, 13, 0)
        end_time = datetime(2025, 2, 12, 18, 0)
        result = service.analyze_free_slots([], start_time, end_time, "test@example.com")
        
        assert result is not None
        assert "suggested_time" in result
        slot_start = datetime.fromisoformat(result["suggested_time"]["start"]).replace(tzinfo=None)
        slot_end = datetime.fromisoformat(result["suggested_time"]["end"]).replace(tzinfo=None)
        assert "+09:00" in result["suggested_time"]["start"]  # JST timezone
        assert 13 <= slot_start.hour < 18  # Afternoon hours
        assert slot_end - slot_start == timedelta(hours=1)  # 1-hour duration

def test_analyze_free_slots():
    """Test free slot detection and conflict prevention."""
    with patch('os.getenv') as mock_getenv, \
         patch('claude_service.Anthropic') as mock_anthropic:
        mock_getenv.return_value = "test-api-key"
        mock_client = MockAnthropicClient(api_key="test-api-key")
        mock_anthropic.return_value = mock_client
        service = ClaudeService()
        
        # Test empty calendar
        start_time = datetime(2025, 2, 12, 9, 0)
        end_time = datetime(2025, 2, 12, 18, 0)
        result = service.analyze_free_slots([], start_time, end_time, "test@example.com")
        
        assert result is not None
        assert "suggested_time" in result
        assert "reason" in result
        slot_start = datetime.fromisoformat(result["suggested_time"]["start"]).replace(tzinfo=None)
        slot_end = datetime.fromisoformat(result["suggested_time"]["end"]).replace(tzinfo=None)
        assert 9 <= slot_start.hour < 18  # Business hours
        assert slot_end - slot_start == timedelta(hours=1)  # 1-hour duration
        
        # Test conflict prevention
        busy_slots = [{
            "start": "2025-02-12T10:00:00+09:00",
            "end": "2025-02-12T11:00:00+09:00"
        }]
        result = service.analyze_free_slots(busy_slots, start_time, end_time, "test@example.com")
        
        assert result is not None
        slot_start = datetime.fromisoformat(result["suggested_time"]["start"]).replace(tzinfo=None)
        slot_end = datetime.fromisoformat(result["suggested_time"]["end"]).replace(tzinfo=None)
        busy_start = datetime.fromisoformat(busy_slots[0]["start"]).replace(tzinfo=None)
        busy_end = datetime.fromisoformat(busy_slots[0]["end"]).replace(tzinfo=None)
        assert not (slot_start < busy_end and slot_end > busy_start)  # No overlap
        
        # Test multiple conflicts
        busy_slots = [
            {
                "start": "2025-02-12T10:00:00+09:00",
                "end": "2025-02-12T11:00:00+09:00"
            },
            {
                "start": "2025-02-12T14:00:00+09:00",
                "end": "2025-02-12T15:00:00+09:00"
            }
        ]
        result = service.analyze_free_slots(busy_slots, start_time, end_time, "test@example.com")
        
        assert result is not None
        slot_start = datetime.fromisoformat(result["suggested_time"]["start"]).replace(tzinfo=None)
        slot_end = datetime.fromisoformat(result["suggested_time"]["end"]).replace(tzinfo=None)
        for busy in busy_slots:
            busy_start = datetime.fromisoformat(busy["start"]).replace(tzinfo=None)
            busy_end = datetime.fromisoformat(busy["end"]).replace(tzinfo=None)
            assert not (slot_start < busy_end and slot_end > busy_start)  # No overlaps
