import pytest
from datetime import datetime, timedelta, timezone
from claude_service import ClaudeService

def test_basic_case():
    """Test case 1: 13:00-15:00 with 13:00-14:00 busy"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 13, 0).replace(tzinfo=jst)
    end_time = datetime(2024, 2, 11, 15, 0).replace(tzinfo=jst)
    
    busy_slots = [{
        "start": "2024-02-11T13:00:00+09:00",
        "end": "2024-02-11T14:00:00+09:00"
    }]
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find 14:00-15:00 slot
    assert len(available_slots) == 1
    slot = available_slots[0]
    assert slot["start"].hour == 14
    assert slot["end"].hour == 15

def test_multiple_busy_slots():
    """Test case 2: 9:00-17:00 with 10:00-11:00 and 14:00-15:00 busy"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 9, 0).replace(tzinfo=jst)
    end_time = datetime(2024, 2, 11, 17, 0).replace(tzinfo=jst)
    
    busy_slots = [
        {
            "start": "2024-02-11T10:00:00+09:00",
            "end": "2024-02-11T11:00:00+09:00"
        },
        {
            "start": "2024-02-11T14:00:00+09:00",
            "end": "2024-02-11T15:00:00+09:00"
        }
    ]
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find slot at 11:00-12:00 (earliest available)
    assert len(available_slots) > 0
    first_slot = available_slots[0]
    assert first_slot["start"].hour == 11
    assert first_slot["end"].hour == 12

def test_consecutive_meetings():
    """Test case 3: 13:00-16:00 with 13:00-14:00 and 15:00-16:00 busy"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 13, 0).replace(tzinfo=jst)
    end_time = datetime(2024, 2, 11, 16, 0).replace(tzinfo=jst)
    
    busy_slots = [
        {
            "start": "2024-02-11T13:00:00+09:00",
            "end": "2024-02-11T14:00:00+09:00"
        },
        {
            "start": "2024-02-11T15:00:00+09:00",
            "end": "2024-02-11T16:00:00+09:00"
        }
    ]
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find 14:00-15:00 slot between meetings
    assert len(available_slots) == 1
    slot = available_slots[0]
    assert slot["start"].hour == 14
    assert slot["end"].hour == 15

def test_business_hours():
    """Test case 4: Respect business hours (9:00-18:00 JST)"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 8, 0).replace(tzinfo=jst)  # Before business hours
    end_time = datetime(2024, 2, 11, 19, 0).replace(tzinfo=jst)   # After business hours
    
    busy_slots = []  # No busy slots
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find first available slot at 9:00
    assert len(available_slots) > 0
    first_slot = available_slots[0]
    assert first_slot["start"].hour == 9
    assert first_slot["end"].hour == 10

def test_no_available_slots():
    """Test case 5: No available slots when fully booked"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 13, 0).replace(tzinfo=jst)
    end_time = datetime(2024, 2, 11, 15, 0).replace(tzinfo=jst)
    
    busy_slots = [
        {
            "start": "2024-02-11T13:00:00+09:00",
            "end": "2024-02-11T15:00:00+09:00"
        }
    ]
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find no available slots
    assert len(available_slots) == 0

def test_timezone_handling():
    """Test case 6: Proper timezone handling"""
    service = ClaudeService()
    
    jst = timezone(timedelta(hours=9))
    start_time = datetime(2024, 2, 11, 13, 0).replace(tzinfo=jst)
    end_time = datetime(2024, 2, 11, 15, 0).replace(tzinfo=jst)
    
    # Use UTC times in busy slots
    busy_slots = [
        {
            "start": "2024-02-11T04:00:00Z",  # 13:00 JST
            "end": "2024-02-11T05:00:00Z"     # 14:00 JST
        }
    ]
    
    available_slots = service.find_available_slots(busy_slots, start_time, end_time)
    
    # Should find 14:00-15:00 JST slot
    assert len(available_slots) == 1
    slot = available_slots[0]
    assert slot["start"].astimezone(jst).hour == 14
    assert slot["end"].astimezone(jst).hour == 15

if __name__ == "__main__":
    pytest.main([__file__])
