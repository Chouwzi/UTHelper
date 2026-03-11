import time
import threading
import logging
from config import settings
from core.client import MoodleClient
from core.parser import MoodleParser
from core.analyzer import AssignmentAnalyzer
from gui.tray import TrayApp
from notifiers.windows import WindowsNotifier

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def background_task(tray_app: TrayApp):
    client = MoodleClient()
    notifier = WindowsNotifier()
    
    logged_in = False
    
    while True:
        try:
            if not logged_in:
                logged_in = client.login()
                
            if logged_in:
                html = client.fetch_timeline_html()
                if html:
                    assignments = MoodleParser.parse_assignments(html)
                    active = AssignmentAnalyzer.filter_active(assignments)
                    sorted_assignments = AssignmentAnalyzer.sort_by_urgency(active)
                    
                    # Update Tray App state
                    tray_app.update_data(sorted_assignments)
                    
                    # Notify user about impending details
                    notifier.notify(sorted_assignments)
                else:
                    logger.warning("Could not fetch timeline.")
            else:
                logger.warning("Still waiting for valid login credentials in .env...")
                
        except Exception as e:
            logger.error(f"Background generic error: {e}")
            logged_in = False # Force relogin on next tick
            
        # Wait for the next check interval
        time.sleep(settings.CHECK_INTERVAL_MINUTES * 60)

def main():
    logger.info("Starting UTH Elearning Alert App...")
    
    # Initialize the Tray App
    tray_app = TrayApp()
    
    # Start the background checker
    bg_thread = threading.Thread(target=background_task, args=(tray_app,))
    bg_thread.daemon = True
    bg_thread.start()
    
    # Run Tray App (Blocks until user exits)
    tray_app.run()
    
if __name__ == "__main__":
    main()
