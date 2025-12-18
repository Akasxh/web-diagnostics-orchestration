from dotenv import load_dotenv
import os
load_dotenv()
from app.config import get_settings
from app.services.ga4_service import run_ga4_queries
from app.services.seo_gsheet_service import get_sheet_names,get_schema_info,execute_workbook_query
from .models import AnalyticsRequest
from contextlib import asynccontextmanager
from fastapi import FastAPI
import json
import uvicorn
from app.orchestrator import taxonomy

# from .models import DiagnosticsRequest, DiagnosticsResponse
# from .orchestrator import Orchestrator

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Check if the file actually exists (Sanity Check)
    if not os.path.exists(settings.GOOGLE_APPLICATION_CREDENTIALS):
        print(f"WARNING: {settings.GOOGLE_APPLICATION_CREDENTIALS} not found! GA4 calls will fail.")
    else:
        # 2. Set the strict environment variable Google looks for.
        # We do this inside Python so it doesn't matter how the shell was configured.
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = settings.GOOGLE_APPLICATION_CREDENTIALS
        print(f"Loaded Google Credentials from: {settings.GOOGLE_APPLICATION_CREDENTIALS}")
    
    yield # Server runs here


app = FastAPI(
    title="Web Diagnostics Orchestration",
    description="API for orchestrating web diagnostics (analytics, SEO, etc.).",
    version="0.1.0",
    lifespan=lifespan
)

#Dummy endpoints.
@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}

@app.get("/creds")
async def getCreds():

    creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS

    if not os.path.exists(creds_path):
        return {"error": f"{creds_path} not found"}

    with open(creds_path, "r") as f:
        creds_data = json.load(f)
    return creds_data


@app.get("/sheets")
async def list_sheet_names():
    """
    Test endpoint to list all sheet names from the configured Google Sheet.
    """
    try:
        names = get_sheet_names()
        return {"sheetNames": names}
    except Exception as e:
        return {"error": str(e)}





@app.post("/query")
async def getAnalytics(request: AnalyticsRequest):
    # return run_ga4_queries(request.propertyId,"Fetch daily page views, total users, and sessions for the /pricing page over the last 14 days.', 'inputs': {'metrics': 'pageViews, totalUsers, sessions', 'dimensions': 'date', 'date_range': 'last 14 days', 'filters': 'pagePath=/pricing', 'order_by': 'date asc', 'property_id': '123456789'}")
    # return taxonomy
    return execute_workbook_query("what percentage of status codes are 200 and what percentage are 301")



if __name__ == "__main__":

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)


