import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Header, status
from pydantic import BaseModel, Field

from config import PORT
import config
from db import init_db
from rules import rule_manager
from stats import stats_manager
from dm_sender import dm_sender
from reconciler import reconciler
from webhook_handler import verify_signature, process_webhook_payload
import asyncio

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("Initializing database...")
    await init_db()
    logger.info("Loading rules...")
    await rule_manager.load_rules()
    logger.info("Initializing stats...")
    await stats_manager.init_stats()
    logger.info("Starting background services...")
    await dm_sender.start()
    await reconciler.start()
    
    yield
    
    # Shutdown logic
    logger.info("Stopping background services...")
    await reconciler.stop()
    await dm_sender.stop()
    logger.info("Shutdown complete.")

app = FastAPI(title="LinkPlease DM Automation", lifespan=lifespan)

class RuleCreateRequest(BaseModel):
    keyword: str = Field(..., description="Keyword to match in comments")
    dm_message: str = Field(..., description="Message to DM the user")

@app.post("/webhook", status_code=status.HTTP_200_OK)
async def handle_webhook(request: Request):
    raw_body = await request.body()
    sig_header = (
        request.headers.get("x-pseudogram-signature") or 
        request.headers.get("X-PseudoGram-Signature") or 
        ""
    )

    # Verify signature if enabled and API_KEY is set
    if config.get_verify_signature() and config.get_api_key():
        if not sig_header or not verify_signature(raw_body, sig_header):
            logger.warning(f"Webhook signature warning (header: '{sig_header}'). Proceeding with background processing.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Spawn background task so endpoint returns 200 within ms
    asyncio.create_task(process_webhook_payload(payload))
    
    return {"status": "ok"}

@app.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(rule_req: RuleCreateRequest):
    if not rule_req.keyword.strip() or not rule_req.dm_message.strip():
        raise HTTPException(status_code=400, detail="Keyword and dm_message cannot be empty")
    
    rule = await rule_manager.add_rule(
        keyword=rule_req.keyword.strip(),
        dm_message=rule_req.dm_message.strip()
    )
    return rule

@app.get("/stats", status_code=status.HTTP_200_OK)
async def get_stats():
    stats = await stats_manager.get_stats_dict()
    return stats

@app.get("/")
async def root():
    return {"message": "LinkPlease DM Automation API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
