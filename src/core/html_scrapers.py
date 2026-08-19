import re
import logging
from html.parser import HTMLParser
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class AssignmentDetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.status = "new"  # new, submitted, grading
        self.files: List[Dict[str, str]] = []
        self._current_tag = ""

    def handle_starttag(self, tag, attrs):
        self._current_tag = tag
        attrs_dict = dict(attrs)
        
        # Check if we are in submission status table
        class_name = attrs_dict.get('class', '')
        if tag == 'td' and 'submissionstatussubmitted' in class_name:
            self.status = "submitted"
        elif tag == 'td' and 'submissionstatusgraded' in class_name:
            self.status = "submitted"
            
        # Detect file links in submission
        if tag == 'a' and 'pluginfile.php' in attrs_dict.get('href', ''):
            href = attrs_dict['href']
            self.files.append({
                'filename': '',
                'fileurl': href,
                'mimetype': ''
            })

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return
            
        if self._current_tag == 'a' and self.files and not self.files[-1]['filename']:
            self.files[-1]['filename'] = data

def scrape_assignment_details(html_content: str) -> Dict[str, Any]:
    """Phân tích HTML trang chi tiết bài tập (/mod/assign/view.php)"""
    parser = AssignmentDetailParser()
    try:
        parser.feed(html_content)
    except Exception as e:
        logger.error(f"Lỗi parse HTML assignment detail: {e}")
        
    return {
        "status": parser.status,
        "plugins": [
            {
                "type": "file",
                "name": "File submissions",
                "fileareas": [
                    {
                        "area": "submission_files",
                        "files": parser.files
                    }
                ]
            }
        ] if parser.files else []
    }
import re
from typing import List, Dict, Any

def scrape_thnn_upcoming_events(html_content: str) -> List[Dict[str, Any]]:
    """Scrape Moodle upcoming events page html to extract assignments."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    events = []
    
    for event_div in soup.find_all('div', attrs={'data-type': 'event'}):
        component = event_div.get('data-event-component', '')
        if component != 'mod_assign':
            continue
            
        course_id = event_div.get('data-course-id', '0')
        title = event_div.get('data-event-title', '').replace(' tới hạn', '').strip()
        
        # Find activity URL
        act_url = ""
        footer = event_div.find('div', class_='card-footer')
        if footer:
            link = footer.find('a')
            if link and link.get('href'):
                act_url = link.get('href')
                
        # Find time
        deadline_ts = 0
        desc = event_div.find('div', class_='description')
        if desc:
            time_link = desc.find('a', href=re.compile(r'time=(\d+)'))
            if time_link:
                m = re.search(r'time=(\d+)', time_link.get('href', ''))
                if m:
                    deadline_ts = int(m.group(1))
                    
            # Find course name
            course_name = ""
            course_link = desc.find('a', href=re.compile(r'course/view\.php'))
            if course_link:
                course_name = course_link.text.strip()
                
        events.append({
            'name': title,
            'course': {'id': int(course_id), 'fullname': course_name},
            'timestart': deadline_ts,
            'url': act_url,
            'modulename': 'assign',
            'eventtype': 'due',
            'action': {'url': act_url} if act_url else {}
        })
        
    return events
