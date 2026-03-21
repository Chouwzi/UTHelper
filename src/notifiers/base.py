from abc import ABC, abstractmethod
from typing import List
from models import Assignment

class BaseNotifier(ABC):
    """
    Abstract Base Class for all Notifiers (Clean Architecture).
    """
    @abstractmethod
    def notify(self, assignments: List[Assignment]) -> bool:
        pass
