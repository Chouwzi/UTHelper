from bs4 import BeautifulSoup
from typing import List
from models import Assignment
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class MoodleParser:
    @staticmethod
    def parse_assignments(html: str) -> List[Assignment]:
        """
        Parses assignments from the Moodle Timeline HTML.
        This focuses on reading the standard Moodle timeline view structure.
        """
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        assignments = []

        # The structure of Moodle blocks depends heavily on the version and theme.
        # This is a generic assumed parsing approach for timeline events.
        events = soup.find_all("div", class_="event")
        
        for event in events:
            try:
                # Extract URL and title
                a_tag = event.find("a", class_="card-link") or event.find("a")
                if not a_tag:
                    continue
                    
                url = a_tag.get("href", "")
                title = a_tag.text.strip()
                
                # Extract course name
                # Course typically in a text muted span or a div
                course_tag = event.find("div", class_="text-muted")
                course_name = course_tag.text.strip() if course_tag else "Unknown Course"
                
                # Extract datetime
                # Usually Moodle timeline lists dates in a clear span
                date_tag = event.find("div", class_="date")
                deadline = datetime.now() # Fallback
                
                # We would use regex or strict datetime parsing here
                # Example: date_str = "Wednesday, 12 April 2026, 11:59 PM"
                # deadline = datetime.strptime(date_str, "%A, %d %B %Y, %I:%M %p")
                # For now using dummy logic until real HTML is reviewed

                assign = Assignment(
                    id=url.split("id=")[-1] if "id=" in url else "unknown",
                    course_id="unknown_course",
                    course_name=course_name,
                    title=title,
                    deadline=deadline,
                    url=url
                )
                assignments.append(assign)
                
            except Exception as e:
                logger.warning(f"Failed to parse an event block: {str(e)}")
                
        return assignments
