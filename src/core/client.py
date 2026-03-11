import requests
from bs4 import BeautifulSoup
from typing import Optional
from config import settings
import logging

logger = logging.getLogger(__name__)

class MoodleClient:
    def __init__(self):
        self.session = requests.Session()
        # Add headers to act like a real browser
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
        })

    def login(self, username: str = None, password: str = None) -> bool:
        """
        Attempts to login to the UTH Moodle platform.
        """
        user = username or settings.UTH_USERNAME
        pwd = password or settings.UTH_PASSWORD

        if not user or not pwd:
            logger.error("Credentials not provided")
            return False

        try:
            # First request to get the login token (logintoken) from the login page
            res = self.session.get(settings.MOODLE_LOGIN_URL)
            res.raise_for_status()

            soup = BeautifulSoup(res.text, "html.parser")
            token_input = soup.find("input", {"name": "logintoken"})
            
            if not token_input:
                logger.error("Could not find login token in Moodle page.")
                return False
                
            token = token_input.get("value")

            # Post the login payload
            payload = {
                "username": user,
                "password": pwd,
                "logintoken": token
            }
            
            login_res = self.session.post(settings.MOODLE_LOGIN_URL, data=payload)
            
            # Successful login usually redirects away from the login page or sets a MoodleSession cookie
            if "login/index.php" not in login_res.url and "MoodleSession" in self.session.cookies.get_dict():
                logger.info("Successfully logged into UTH Moodle.")
                return True
                
            logger.warning("Failed to login, please check your credentials.")
            return False
            
        except Exception as e:
            logger.error(f"Login exception: {str(e)}")
            return False

    def fetch_timeline_html(self) -> Optional[str]:
        """
        Fetches the user's timeline / dashboard where assignments are usually listed.
        """
        # Attempting to fetch the dashboard page where assignments are shown
        url = f"{settings.MOODLE_BASE_URL}/my/"
        try:
            res = self.session.get(url)
            res.raise_for_status()
            return res.text
        except Exception as e:
            logger.error(f"Failed to fetch timeline: {str(e)}")
            return None

    # TBD: We will add Web Service API logic here if UTH enables it
