from abc import ABC, abstractmethod
from typing import Dict, Any, List

class IntegrationProvider(ABC):
    """
    Base class for all 3rd-party integrations in the SemanticOS Marketplace.
    """
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Returns the name of the integration provider."""
        pass
        
    @abstractmethod
    def fetch_logs(self, query: str, time_range: str) -> List[Dict[str, Any]]:
        """Fetch logs from the external system."""
        pass
        
    @abstractmethod
    def send_alert(self, incident: Any) -> bool:
        """Send an alert or incident to the external system."""
        pass
        
    @abstractmethod
    def sync_metadata(self) -> Dict[str, Any]:
        """Synchronize metadata (e.g., deployments, tags) from the external system."""
        pass
