import time, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from core.parser import MoodleParser


def generate_activity_html(size_kb=10):
    base = '<html><body><h3 class="h2">Sample Activity</h3>'
    base += '<div class="page-banner-content"><h2>Course Full Name</h2></div>'
    base += '<table class="generaltable"><tr><th>Submission status</th><td>Not submitted</td></tr></table>'
    paras = '<p>' + ('Lorem ipsum ' * 40) + '</p>'
    base += paras * (size_kb)
    base += '</body></html>'
    return base


def generate_calendar_html(n=500):
    items = []
    for i in range(n):
        items.append(f'<li data-region="event-item" data-event-eventtype="due" data-event-component="mod_assign" data-courseid="{i%10}" data-event-id="{1000+i}"><a data-action="view-event" href="/mod/assign/view.php?id={1000+i}"><span class="eventname">Assignment {i}</span></a></li>')
    html = '<html><body><select name="course"><option value="1">Course 1</option></select><ul>'+''.join(items)+'</ul></body></html>'
    return html


def test_parse_activity_page_perf():
    html = generate_activity_html(size_kb=10)
    runs=10
    start=time.perf_counter()
    for _ in range(runs):
        MoodleParser.parse_activity_page(html, url="https://example.com/mod/assign/view.php?id=123")
    elapsed=time.perf_counter()-start
    print(f"parse_activity_page: {runs} runs took {elapsed:.3f}s, avg {elapsed/runs:.3f}s")


def test_parse_assignments_perf():
    html = generate_calendar_html(n=1000)
    start=time.perf_counter()
    assignments = MoodleParser.parse_assignments(html)
    elapsed=time.perf_counter()-start
    print(f"parse_assignments: parsed {len(assignments)} items in {elapsed:.3f}s")
