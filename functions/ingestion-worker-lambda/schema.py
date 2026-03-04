from pydantic import BaseModel
from typing import Literal, Optional


class Resource(BaseModel):
    id: str
    type: str
    workspace_id: Optional[str] = None


class IngestionMessage(BaseModel):
    event_id: str
    source: Literal["notion", "google_drive", "bookstack"]
    event_type: Literal["added", "modified", "deleted"]
    resource: Resource
    fetch_mode: Literal["api", "export"]