"""
Pydantic request/response models for the API.

This is just an example file , need to edit this for our usecase.

"""

from pydantic import BaseModel, Field
from typing import Optional

class AnalyticsRequest(BaseModel):
    query: str
    propertyId: Optional[str] = None
