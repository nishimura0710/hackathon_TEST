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
            slot_start = slot_start.astimezone(utc)
            slot_end = slot_end.astimezone(utc)
            
            # Convert to JST for business hours check
            jst = timezone(timedelta(hours=9))
            jst_start = slot_start.astimezone(jst)
            jst_end = slot_end.astimezone(jst)
            
            # Check business hours (9:00-18:00 JST)
            if not (9 <= jst_start.hour < 18 and 9 <= jst_end.hour <= 18):
                return False
                
            # Check for conflicts with existing events
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"].replace('Z', '+00:00')).astimezone(utc)
                busy_end = datetime.fromisoformat(busy["end"].replace('Z', '+00:00')).astimezone(utc)
                
                # Check overlap
                if (slot_start < busy_end and slot_end > busy_start):
                    return False
                    
            return True
        except Exception as e:
            print(f"Validation error: {str(e)}")
            return False
    
    def find_available_slots(self, busy_slots, start_time, end_time):
        """Find available time slots within the given range."""
        available_slots = []
        
        # Convert busy slots to datetime objects and sort them
        busy_periods = []
        for slot in busy_slots:
            slot_start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00'))
            slot_end = datetime.fromisoformat(slot["end"].replace('Z', '+00:00'))
            busy_periods.append((slot_start, slot_end))
        
        busy_periods.sort(key=lambda x: x[0])
        
        # If no busy periods, return first available hour
        if not busy_periods:
            available_slots.append({
                "start": start_time,
                "end": start_time + timedelta(hours=1)
            })
            return available_slots
            
        # Start from the end of first busy period
        current = busy_periods[0][1]
        
        # Find gaps between busy periods
        for i in range(len(busy_periods)):
            # If this is not the last busy period, check gap until next busy period
            if i < len(busy_periods) - 1:
                next_start = busy_periods[i + 1][0]
                if current + timedelta(hours=1) <= next_start:
                    available_slots.append({
                        "start": current,
                        "end": current + timedelta(hours=1)
                    })
            current = busy_periods[i][1]
        
        # Check final period after last busy slot
        if current + timedelta(hours=1) <= end_time:
            available_slots.append({
                "start": current,
                "end": current + timedelta(hours=1)
            })
            
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
                    "suggested_time": {
                        "start": start_time.isoformat(),
                        "end": end_time.isoformat()
                    },
                    "reason": "指定された時間帯に空き時間が見つかりませんでした。別の時間帯をお試しください。"
                }
            
            # Select the first available slot (earliest time)
            selected_slot = available_slots[0]
            slot_start = selected_slot["start"].astimezone(jst)
            slot_end = selected_slot["end"].astimezone(jst)
            
            # Format times for response
            return {
                "suggested_time": {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat()
                },
                "reason": f"{slot_start.strftime('%H:%M')}から{slot_end.strftime('%H:%M')}の時間帯が空いているため、この時間に予定を登録します。"
            }
            
        except Exception as e:
            print(f"Error analyzing free slots: {str(e)}")
            return {
                "suggested_time": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                },
                "reason": "予定の確認中にエラーが発生しました。別の時間帯をお試しください。"
            }
