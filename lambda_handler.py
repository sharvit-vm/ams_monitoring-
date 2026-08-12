"""AWS Lambda entry point for the FastAPI application.

API Gateway invokes `handler`; Mangum adapts the ASGI FastAPI app in server.py
to Lambda's request/response event shape.
"""

import os

from mangum import Mangum


os.environ.setdefault("CLONE_ROOT", "/tmp/clone")
os.environ.setdefault("CACHE_DIR", "/tmp/cache")
os.environ.setdefault("QUEUE_FILE", "/tmp/data/event_queue.json")
os.environ.setdefault("RCA_REPORT_DIR", "/tmp/data/rca_reports")

from server import app


handler = Mangum(app, lifespan="off")
