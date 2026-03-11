from typing import List
from models import Assignment, UrgencyLevel

class AssignmentAnalyzer:
    @staticmethod
    def sort_by_urgency(assignments: List[Assignment]) -> List[Assignment]:
        """
        Sort assignments putting critical ones first, sorted by deadline ascending.
        """
        def get_priority(a: Assignment) -> int:
            if a.urgency == UrgencyLevel.CRITICAL:
                return 0
            elif a.urgency == UrgencyLevel.WARNING:
                return 1
            return 2

        return sorted(assignments, key=lambda x: (get_priority(x), x.deadline))
    
    @staticmethod
    def filter_active(assignments: List[Assignment]) -> List[Assignment]:
        """
        Removes assignments that have already passed their deadline.
        """
        return [a for a in assignments if a.hours_remaining > 0]
