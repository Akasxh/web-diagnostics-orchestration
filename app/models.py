"""
Pydantic request/response models for the API.

This is just an example file , need to edit this for our usecase.

"""

from pydantic import BaseModel, Field


class DiagnosticsRequest(BaseModel):
    url: str = Field(..., description="Target URL to run diagnostics against.")
    include_analytics: bool = Field(
        default=True, description="Whether to run analytics (GA4) diagnostics."
    )
    include_seo: bool = Field(
        default=True, description="Whether to run SEO (Screaming Frog style) diagnostics."
    )


class AnalyticsResult(BaseModel):
    pageviews: int | None = None
    sessions: int | None = None
    notes: str | None = None


class SEOResult(BaseModel):
    title: str | None = None
    status_code: int | None = None
    notes: str | None = None


class DiagnosticsResponse(BaseModel):
    url: str
    analytics: AnalyticsResult | None = None
    seo: SEOResult | None = None


