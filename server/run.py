import uvicorn
import sys
import os
from app.config import settings

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        limit_concurrency=settings.HTTP_MAX_CONCURRENCY,
        timeout_keep_alive=settings.HTTP_KEEPALIVE_TIMEOUT_SECONDS,
        log_level="info",
    )
