import time, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from core.data_orchestrator import DataOrchestrator


def _sample_activity_html():
    return '<html><body><h3 class="h2">Activity</h3><div id="intro">' + ('<p>desc</p>'*200) + '</div></body></html>'


def test_prefetch_all_details_perf():
    orchestrator = DataOrchestrator()
    # monkeypatch client.fetch_url to return sample html synchronously
    orchestrator.client.fetch_url = lambda url: _sample_activity_html()
    activities = [{'url': f'https://example.com/mod/assign/view.php?id={i}', 'title': f'A{i}', 'deadline': '2099-12-31T23:59:59'} for i in range(50)]
    start=time.perf_counter()
    done = orchestrator.prefetch_all_details(activities, workers=4, cancel_flag=None, force_refresh=True)
    elapsed=time.perf_counter()-start
    print(f"prefetch_all_details: fetched {done} items in {elapsed:.3f}s")
