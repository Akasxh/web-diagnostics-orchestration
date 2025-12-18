## web-diagnostics-orchestration

FastAPI-based service for orchestrating web diagnostics (analytics, SEO, etc.).

---

## Installation

- **Prerequisites**
  - **Python**: 3.11 (pinned via `.python-version`)
  - **uv**: Python package/dependency manager (`pip install uv` or see uv docs)

- **Clone the repository**

```bash
git clone <YOUR_REPO_URL> web-diagnostics-orchestration
cd web-diagnostics-orchestration
```


- **Install dependencies with uv**

```bash
uv sync
```

This will create a virtual environment and install all dependencies from `pyproject.toml` / `uv.lock`.

---

## Configuration

- **Environment variables**
  - Managed via `app/config.py` (`Settings` class).
  - You can create a `.env` file in the project root, for example:

```bash
APP_ENV=development
GA4_PROPERTY_ID=<your-ga4-property-id>
LITELLM_PROXY_URL=<your-litellm-proxy-url>
SERVICE_ACCOUNT_MAIL=<your-service-account-email>
```

- **Credentials**
  - Place your Google service account JSON as `credentials.json` in the project root (same level as `pyproject.toml`).

---

## Running the API

- **Using uv + python (development)**

```bash
uv run python -m app.main
```

This will start FastAPI with Uvicorn on `http://0.0.0.0:8080` (see `app/main.py`).

- **Using uvicorn directly**

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Basic Endpoints

- **Health check**
  - **Method**: `GET`
  - **Path**: `/health`
  - **Description**: Simple status endpoint to verify the service is running.

You can open the interactive API docs once the server is running:

- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
