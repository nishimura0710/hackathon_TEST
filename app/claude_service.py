from datetime import datetime, timedelta, timezone
import json

class ClaudeService:
    def __init__(self):
        pass
    
    def validate_time_slot(self, slot_start, slot_end, busy_slots):
        """Validate that a suggested time slot doesn't overlap with busy slots."""
        try:
            # Convert all times to UTC for comparison
            utc = timezone.utc
            jst = timezone(timedelta(hours=9))
            
            # Ensure times are timezone-aware and in UTC
            slot_start = slot_start.astimezone(utc)
            slot_end = slot_end.astimezone(utc)
            
            # Convert to JST for business hours check
            jst_start = slot_start.astimezone(jst)
            jst_end = slot_end.astimezone(jst)
            
            # Check business hours (9:00-18:00 JST)
            if not (9 <= jst_start.hour < 18 and 9 <= jst_end.hour <= 18):
                print(f"Outside business hours: {jst_start.hour}:00-{jst_end.hour}:00")
                return False
            
            # Check duration is exactly 1 hour
            if (slot_end - slot_start) != timedelta(hours=1):
                print("Invalid duration - must be exactly 1 hour")
                return False
            
            # Check for conflicts with existing events
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"].replace('Z', '+00:00')).astimezone(utc)
                busy_end = datetime.fromisoformat(busy["end"].replace('Z', '+00:00')).astimezone(utc)
                
                # Check overlap
                if (slot_start < busy_end and slot_end > busy_start):
                    print(f"Conflict with existing event: {busy_start.astimezone(jst)} - {busy_end.astimezone(jst)}")
                    return False
            
            return True
        except Exception as e:
            print(f"Validation error: {str(e)}")
            return False
    
    def find_available_slots(self, busy_slots, start_time, end_time):
        """Find available time slots within the given range."""
        available_slots = []
        current = start_time  # Start from the requested start time
        
        # Convert busy slots to datetime objects and sort them
        busy_periods = []
        for slot in busy_slots:
            slot_start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00'))
            slot_end = datetime.fromisoformat(slot["end"].replace('Z', '+00:00'))
            busy_periods.append((slot_start, slot_end))
        
        busy_periods.sort(key=lambda x: x[0])
        
        # Check each hour in the requested range
        while current + timedelta(hours=1) <= end_time:
            # Check if any busy period affects the current time
            for busy_start, busy_end in busy_periods:
                if busy_start <= current + timedelta(hours=1):
                    if busy_end > current:
                        current = busy_end
                        break
            
            # If we've gone past the end time, stop
            if current + timedelta(hours=1) > end_time:
                break
            
            slot_end = current + timedelta(hours=1)
            
            # Check if this slot would be valid
            if not self.validate_time_slot(current, slot_end, busy_slots):
                current += timedelta(hours=1)
                continue
            
            # Check if it overlaps with any future busy periods
            is_available = True
            for busy_start, busy_end in busy_periods:
                if current < busy_end and slot_end > busy_start:
                    is_available = False
                    break
            
            # If slot is available, add it and return
            if is_available:
                available_slots.append({
                    "start": current,
                    "end": slot_end
                })
                return available_slots
            
            # Move to next hour if no valid slot found
            current += timedelta(hours=1)
        
        return available_slots

    def analyze_free_slots(self, busy_slots, start_time, end_time, calendar_id, message=""):
        """Analyze and find available time slots."""
        try:
            # Convert times to JST for consistent handling
            jst = timezone(timedelta(hours=9))
            start_time = start_time.astimezone(jst)
            end_time = end_time.astimezone(jst)
            
            # Find available slots
            available_slots = self.find_available_slots(busy_slots, start_time, end_time)
            
            if not available_slots:
                return {
                    "suggested_time": None,
                    "reason": f"{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}の間に空き時間が見つかりませんでした。"
                }
            
            # Select the first available slot (earliest time)
            selected_slot = available_slots[0]
            slot_start = selected_slot["start"].astimezone(jst)
            slot_end = selected_slot["end"].astimezone(jst)
            
            # Format times for response with clearer explanation
            return {
                "suggested_time": {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat()
                },
                "reason": f"{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}の時間枠で、"
                         f"{slot_start.strftime('%H:%M')}から{slot_end.strftime('%H:%M')}が空いているため、"
                         f"この時間に予定を登録します。"
            }
            
        except Exception as e:
            print(f"Error analyzing free slots: {str(e)}")
            return {
                "suggested_time": None,
                "reason": "予定の確認中にエラーが発生しました。別の時間帯をお試しください。"
            }
