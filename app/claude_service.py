from datetime import datetime, timedelta, timezone
import json

class ClaudeService:
    def __init__(self):
        self.jst = timezone(timedelta(hours=9))
    
    def validate_time_slot(self, slot_start, slot_end, busy_slots):
        """Validate that a suggested time slot doesn't overlap with busy slots."""
        try:
            # Ensure times are in JST
            slot_start = slot_start.astimezone(self.jst)
            slot_end = slot_end.astimezone(self.jst)
            
            # Check business hours (9:00-18:00 JST)
            if not (9 <= slot_start.hour < 18 and 9 <= slot_end.hour <= 18):
                return False
            
            # Check duration is exactly 1 hour
            if (slot_end - slot_start) != timedelta(hours=1):
                return False
            
            # Check for conflicts with existing events
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"].replace('Z', '+00:00')).astimezone(self.jst)
                busy_end = datetime.fromisoformat(busy["end"].replace('Z', '+00:00')).astimezone(self.jst)
                
                # Check for any overlap
                if not (slot_end <= busy_start or slot_start >= busy_end):
                    return False
            
            return True
        except Exception as e:
            return False
    
    def find_available_slots(self, busy_slots, start_time, end_time):
        """Find available time slots within the given range."""
        available_slots = []
        
        # Convert busy slots to datetime objects and sort them
        busy_periods = []
        for slot in busy_slots:
            busy_start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00')).astimezone(self.jst)
            busy_end = datetime.fromisoformat(slot["end"].replace('Z', '+00:00')).astimezone(self.jst)
            busy_periods.append((busy_start, busy_end))
        
        busy_periods.sort(key=lambda x: x[0])
        
        # Ensure we stay within business hours
        current = max(
            start_time.astimezone(self.jst),
            start_time.astimezone(self.jst).replace(hour=9, minute=0)
        )
        end_time = min(
            end_time.astimezone(self.jst),
            end_time.astimezone(self.jst).replace(hour=18, minute=0)
        )
        target_date = current.date()
        
        # Find first busy period that affects our time range
        first_busy = None
        for busy_start, busy_end in busy_periods:
            if busy_start.date() == target_date:
                if busy_start >= current:
                    first_busy = (busy_start, busy_end)
                    break
        
        # If there's a busy period coming up, skip to after it
        if first_busy:
            current = first_busy[1]
        
        # Find all available slots
        while current + timedelta(hours=1) <= end_time:
            if current.date() != target_date:
                break
            
            slot_end = current + timedelta(hours=1)
            
            # Skip if outside business hours
            if current.hour < 9:
                current = current.replace(hour=9, minute=0)
                continue
            elif slot_end.hour > 18:
                break
            
            # Check if this slot overlaps with any busy periods
            overlaps = False
            for busy_start, busy_end in busy_periods:
                if busy_start.date() == target_date:
                    # If busy period overlaps with our slot
                    if busy_start < slot_end and busy_end > current:
                        overlaps = True
                        current = busy_end  # Skip to end of busy period
                        break
            
            # If no overlap and valid slot, add it
            if not overlaps and self.validate_time_slot(current, slot_end, busy_slots):
                available_slots.append({
                    "start": current,
                    "end": slot_end
                })
            
            # Always move forward by 1 hour
            if not overlaps:
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
