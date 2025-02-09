import pytest
from datetime import datetime, timedelta
from claude_service import ClaudeService

from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_analyze_free_slots():
    with patch('os.getenv') as mock_getenv, \
         patch('anthropic.Anthropic') as mock_anthropic:
        # Mock environment variable
        mock_getenv.return_value = "test-api-key"
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        
        # Configure mock responses for each test case
        def mock_create(**kwargs):
            prompt = kwargs.get('messages', [{}])[0].get('content', '')
            if '"busy": []' in prompt:
                # Empty calendar case
                return AsyncMock(content=[AsyncMock(text='''{
                    "suggested_time": {
                        "start": "2025-02-12T09:00:00+09:00",
                        "end": "2025-02-12T10:00:00+09:00"
                    },
                    "reason": "This is the earliest available slot"
                }''')])
            elif '"start": "2025-02-12T10:00:00+09:00"' in prompt and '"end": "2025-02-12T11:00:00+09:00"' in prompt:
                # Single busy slot case
                return AsyncMock(content=[AsyncMock(text='''{
                    "suggested_time": {
                        "start": "2025-02-12T11:15:00+09:00",
                        "end": "2025-02-12T12:15:00+09:00"
                    },
                    "reason": "This slot starts after the busy period with buffer"
                }''')])
            else:
                # Multiple busy slots case
                return AsyncMock(content=[AsyncMock(text='''{
                    "suggested_time": {
                        "start": "2025-02-12T12:00:00+09:00",
                        "end": "2025-02-12T13:00:00+09:00"
                    },
                    "reason": "This slot is between the busy periods"
                }''')])
        
        mock_client.messages.create = AsyncMock(side_effect=mock_create)
        
        service = ClaudeService()
        
        # Test case 1: Empty calendar
        start_time = datetime(2025, 2, 12, 9, 0)
        end_time = datetime(2025, 2, 12, 18, 0)
        busy_slots = []
        
        result = await service.analyze_free_slots(
            busy_slots,
            start_time,
            end_time,
            "test@example.com"
        )
        
        assert result is not None
    assert "suggested_time" in result
    assert "reason" in result
    
    # Verify business hours and duration
    suggested = result["suggested_time"]
    slot_start = datetime.fromisoformat(suggested["start"]).replace(tzinfo=None)
    slot_end = datetime.fromisoformat(suggested["end"]).replace(tzinfo=None)
    assert 9 <= slot_start.hour < 18
    assert 9 <= slot_end.hour <= 18
    assert slot_end - slot_start == timedelta(hours=1)
    
    # Test case 2: With existing events
    busy_slots = [
        {
            "start": "2025-02-12T10:00:00+09:00",
            "end": "2025-02-12T11:00:00+09:00"
        }
    ]
    
    result = await service.analyze_free_slots(
        busy_slots,
        start_time,
        end_time,
        "test@example.com"
    )
    
    assert result is not None
    suggested = result["suggested_time"]
    slot_start = datetime.fromisoformat(suggested["start"]).replace(tzinfo=None)
    slot_end = datetime.fromisoformat(suggested["end"]).replace(tzinfo=None)
    
    # Verify no overlap with busy slot
    busy_start = datetime(2025, 2, 12, 10, 0)
    busy_end = datetime(2025, 2, 12, 11, 0)
    assert not (slot_start < busy_end and slot_end > busy_start)
    
    # Verify business hours and duration
    assert 9 <= slot_start.hour < 18
    assert 9 <= slot_end.hour <= 18
    assert slot_end - slot_start == timedelta(hours=1)
    
    # Test case 3: Multiple busy slots
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
    
    result = await service.analyze_free_slots(
        busy_slots,
        start_time,
        end_time,
        "test@example.com"
    )
    
    assert result is not None
    suggested = result["suggested_time"]
    slot_start = datetime.fromisoformat(suggested["start"]).replace(tzinfo=None)
    slot_end = datetime.fromisoformat(suggested["end"]).replace(tzinfo=None)
    
    # Verify no overlap with any busy slot
    for busy in busy_slots:
        busy_start = datetime.fromisoformat(busy["start"]).replace(tzinfo=None)
        busy_end = datetime.fromisoformat(busy["end"]).replace(tzinfo=None)
        assert not (slot_start < busy_end and slot_end > busy_start)
    
    # Verify business hours and duration
    assert 9 <= slot_start.hour < 18
    assert 9 <= slot_end.hour <= 18
    assert slot_end - slot_start == timedelta(hours=1)
