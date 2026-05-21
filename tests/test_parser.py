import pytest
from bs4 import BeautifulSoup
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from core.parser import MoodleParser

def test_extract_title():
    html = '<html><body><h3 class="h2">Test Assignment Title</h3></body></html>'
    soup = BeautifulSoup(html, 'html.parser')
    assert MoodleParser._extract_title(soup) == "Test Assignment Title"

    # Fallback to h2
    html2 = '<html><body><h2>Title H2</h2></body></html>'
    soup2 = BeautifulSoup(html2, 'html.parser')
    assert MoodleParser._extract_title(soup2) == "Title H2"

def test_extract_course_names():
    html_banner = '''
    <div class="page-banner-content"><h2>Full Course Name 101</h2></div>
    <ol class="breadcrumb">
        <li><a href="course/view.php?id=1" title="Short Name">Short Name</a></li>
    </ol>
    '''
    soup = BeautifulSoup(html_banner, 'html.parser')
    short_name, full_name = MoodleParser._extract_course_names(soup)
    assert short_name == "Short Name"
    assert full_name == "Full Course Name 101"

def test_extract_status_data():
    html = '''
    <table class="generaltable">
        <tr><th>Submission status</th><td>Submitted for grading</td></tr>
        <tr><th>Grading status</th><td>Not graded</td></tr>
    </table>
    '''
    soup = BeautifulSoup(html, 'html.parser')
    status_data = MoodleParser._extract_status_data(soup)
    assert status_data["Submission status"] == "Submitted for grading"
    assert status_data["Grading status"] == "Not graded"

def test_determine_submission_status():
    soup = BeautifulSoup("<html></html>", 'html.parser')
    
    status_data_submitted = {"submission status": "submitted for grading"}
    assert MoodleParser._determine_submission_status(soup, status_data_submitted, "unknown") == "submitted"

    status_data_not_submitted = {"Trạng thái nộp": "Chưa nộp"}
    assert MoodleParser._determine_submission_status(soup, status_data_not_submitted, "unknown") == "not_submitted"

    status_data_graded = {"Grading status": "Graded", "Submission status": "Submitted for grading"}
    assert MoodleParser._determine_submission_status(soup, status_data_graded, "unknown") == "graded"

def test_extract_quiz_info():
    html = '''
    <div class="quizinfo">
        <p>Attempts allowed: 2</p>
        <p>Time limit: 30 mins</p>
    </div>
    <div class="quizattempt">No more attempts allowed</div>
    '''
    soup = BeautifulSoup(html, 'html.parser')
    info_list, attempts, time_limit, status = MoodleParser._extract_quiz_info(soup)
    assert attempts == "2"
    assert time_limit == "30 mins"
    assert status == "submitted" # Due to "No more attempts"


def test_parse_activity_page_uses_lxml_parser(monkeypatch):
    parsers = []
    real_beautiful_soup = MoodleParser.parse_activity_page.__globals__["BeautifulSoup"]

    def recording_soup(markup, parser, *args, **kwargs):
        parsers.append(parser)
        return real_beautiful_soup(markup, parser, *args, **kwargs)

    monkeypatch.setitem(MoodleParser.parse_activity_page.__globals__, "BeautifulSoup", recording_soup)

    MoodleParser.parse_activity_page("<html><body><h3 class='h2'>A</h3></body></html>", "https://example.com/mod/assign/view.php?id=1")

    assert parsers[0] == "lxml"


def test_extract_description_keeps_only_sanitized_inner_content():
    html = """
    <html><body>
      <div id="intro"><p>Hello</p><script>alert(1)</script></div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")

    description = MoodleParser._extract_description(soup)

    assert "<html" not in description.lower()
    assert "<body" not in description.lower()
    assert "id=\"intro\"" not in description.lower()
    assert "<script" not in description.lower()
    assert "<p>Hello</p>" in description
