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
from openai import OpenAI

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




from agent import run_graph
@app.post("/query")
async def getAnalytics(request: AnalyticsRequest):
    # query and propertyID
    # return run_ga4_queries(request.propertyId,"Fetch daily page views, total users, and sessions for the /pricing page over the last 14 days.', 'inputs': {'metrics': 'pageViews, totalUsers, sessions', 'dimensions': 'date', 'date_range': 'last 14 days', 'filters': 'pagePath=/pricing', 'order_by': 'date asc', 'property_id': '123456789'}")
    # return taxonomy

    async def llm_detect_json_requirement(query: str) -> bool:
        """
        Calls LLM to check if the user is demanding JSON output. LLM must respond:
        {
        "isJson": "True" or "False"
        }
        """
        client = OpenAI(
            api_key= settings.LITELLM_KEY,
            base_url=settings.LITELLM_PROXY_URL
        )
        prompt = f"""
                Given the following user query, is the user explicitly asking for the answer/output/result to be in JSON format? 
                Respond ONLY in JSON with this format:
                {{
                "isJson": "True" or "False"
                }}
                User Query:
                \"\"\"{query}\"\"\"
                """
        
        response = client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user"} # Convert dict to str if needed
            ]
        )

        # Clean potential markdown wrapping
        raw = response.choices[0].message.content.strip()
        try:
            result = json.loads(raw)
            return result.get("isJson", "False") == "True"
        except Exception:
            return False
        
    shouldBeJson = llm_detect_json_requirement(request.query)
    return run_graph(request.query,request.propertyId,shouldBeJson)




if __name__ == "__main__":

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)


