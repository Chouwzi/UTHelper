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
