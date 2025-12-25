import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, JSONResponse
from supabase import create_client, Client
from app.schemas import AnalysisRequestCreate, AnalysisRequestResponse, FeedbackCreate
from app.routers import feedback, news_analysis
from app.services.news_analysis import NewsAnalysisService
from app.services import get_analyzer
from app.api.v1.endpoints import exa_service, image_analysis
from typing import Optional, Dict, Any
from datetime import datetime
from dotenv import load_dotenv, dotenv_values
import logging
from app.core.http_client import get_http_session, cleanup_http_session
from pydantic import BaseModel
from app.services.url_analysis import URLAnalysisService, URLAnalysisResponse
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(override=True)

# Application lifecycle management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize services and create HTTP session
    app.state.http_session = await get_http_session()
    app.state.news_service = NewsAnalysisService()
    app.state.writing_style_analyzer = get_analyzer()
    
    # Initialize URL Analysis Service
    app.state.url_analysis_service = URLAnalysisService()
    
    yield
    
    # Shutdown: Cleanup resources
    await cleanup_http_session()
    if hasattr(app.state, 'news_service'):
        await app.state.news_service.cleanup()

# Initialize FastAPI app with ORJSON response and lifecycle management
app = FastAPI(
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    title="YEAH News Detection API",
    description="API for analyzing news articles with persistent connections"
)

# Get port from environment variable
port = int(os.environ.get("PORT", 10000))
host = "0.0.0.0"

# Configure CORS with more specific settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "chrome-extension://*",  # Allow Chrome extension
        f"http://{host}:{port}",  # API server
        "https://*.onrender.com",  # Render domains
        "*"  # Fallback for development
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=[
        "Content-Type",
        "Accept",
        "Accept-Language",
        "Origin",
        "Authorization",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
        "Access-Control-Allow-Origin"
    ],
    expose_headers=["*"],
    max_age=3600
)

# Include routers
app.include_router(feedback.router)
app.include_router(news_analysis.router, prefix="/api")
app.include_router(exa_service.router, prefix="/api")
app.include_router(image_analysis.router, prefix="/api/v1/image-analysis")

# Initialize Supabase client with debug logging
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

logger.debug(f"SUPABASE_URL: {'set' if SUPABASE_URL else 'not set'}")
logger.debug(f"SUPABASE_KEY: {'set' if SUPABASE_KEY else 'not set'}")

if not SUPABASE_URL or not SUPABASE_KEY:
    # Try loading directly from .env file as fallback
    config = dotenv_values(".env")
    SUPABASE_URL = config.get("SUPABASE_URL")
    SUPABASE_KEY = config.get("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Health check endpoint for Render
@app.get("/health")
async def health_check():
    """Health check endpoint for Render"""
    return {"status": "healthy"}

# Add OPTIONS and HEAD method handlers for root endpoint
@app.options("/")
@app.head("/")
async def handle_preflight():
    return {}

@app.get("/")
async def root():
    return {"status": "healthy"}

# Add OPTIONS handlers for all API endpoints
@app.options("/analysis_requests/")
async def analysis_requests_preflight():
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
            "Access-Control-Max-Age": "3600",
        }
    )

@app.post("/analysis_requests/", response_model=AnalysisRequestResponse)
async def create_analysis_request(request: AnalysisRequestCreate):
    try:
        data = {
            "content_type": request.content_type,
            "content": request.content,
            "user_id": request.user_id,
            "submission_date": datetime.utcnow().isoformat()
        }
        response = supabase.table("analysis_requests").insert(data).execute()
        if response.status_code != 201:
            raise HTTPException(status_code=400, detail="Failed to create analysis request")
        return JSONResponse(
            content=response.data[0],
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept, Origin"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.options("/feedback")
async def feedback_preflight():
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
            "Access-Control-Max-Age": "3600",
        }
    )

@app.post("/feedback")
async def create_feedback(feedback: FeedbackCreate):
    try:
        data = {
            "feedback_text": feedback.feedback,
            "created_at": datetime.utcnow().isoformat()
        }
        response = supabase.table("feedback").insert(data).execute()
        if response.status_code != 201:
            raise HTTPException(status_code=400, detail="Failed to submit feedback")
        return JSONResponse(
            content={"status": "success", "message": "Feedback submitted successfully"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept, Origin"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.options("/api/reverse-searchy")
async def reverse_search_preflight():
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
            "Access-Control-Max-Age": "3600",
        }
    )

@app.post("/api/reverse-searchy")
async def reverse_search(query: Dict[str, Any], background_tasks: BackgroundTasks):
    try:
        logger.info(f"Received analysis request: {query}")
        result = await app.state.news_service.analyze_news(query.get("content", ""), background_tasks)
        logger.info(f"Analysis result: {result}")
        
        # Ensure the response has all required fields
        if not all(key in result for key in ["ISFAKE", "CONFIDENCE", "EXPLANATION_EN", "EXPLANATION_ML"]):
            logger.error(f"Missing required fields in result: {result}")
            result = {
                "ISFAKE": 1,
                "CONFIDENCE": 0.5,
                "EXPLANATION_EN": "System error: Invalid response format",
                "EXPLANATION_ML": "സിസ്റ്റം പിശക്: അസാധുവായ പ്രതികരണ ഫോർമാറ്റ്"
            }

        # Ensure proper types for each field
        result["ISFAKE"] = int(result.get("ISFAKE", 1))
        result["CONFIDENCE"] = float(result.get("CONFIDENCE", 0.5))
        result["EXPLANATION_EN"] = str(result.get("EXPLANATION_EN", "No explanation available"))
        result["EXPLANATION_ML"] = str(result.get("EXPLANATION_ML", "വിശദീകരണം ലഭ്യമല്ല"))

        return JSONResponse(
            content=result,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
                "Content-Type": "application/json"
            }
        )
    except Exception as e:
        logger.error(f"Error in reverse search: {str(e)}", exc_info=True)
        error_response = {
            "ISFAKE": 1,
            "CONFIDENCE": 0.5,
            "EXPLANATION_EN": f"Analysis failed: {str(e)}",
            "EXPLANATION_ML": "വിശകലനം പരാജയപ്പെട്ടു"
        }
        return JSONResponse(
            status_code=200,  # Return 200 even for errors to handle them in the frontend
            content=error_response,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
                "Content-Type": "application/json"
            }
        )

@app.options("/api/writing-style")
async def writing_style_preflight():
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
            "Access-Control-Max-Age": "3600",
        }
    )

@app.post("/api/writing-style")
async def analyze_writing_style(query: Dict[str, str]):
    try:
        content = query.get("content", "")
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        
        logger.debug(f"Analyzing text of length: {len(content)}")
        result = app.state.writing_style_analyzer.analyze_text(content)
        
        logger.debug(f"Analysis results: {result}")
        if all(score == 0 for score in result.values()):
            logger.warning("All scores returned as zero - possible analysis failure")
            
        return JSONResponse(
            content=result,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept, Origin"
            }
        )
    except Exception as e:
        logger.error(f"Error in writing style analysis: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Add OPTIONS handlers for API endpoints
@app.options("/api/writing-style")
async def writing_style_preflight():
    return {}

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail)},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Origin",
        }
    )

class URLAnalysisRequest(BaseModel):
    url: str

@app.post("/api/analyze-url", response_model=URLAnalysisResponse)
async def analyze_url(request: URLAnalysisRequest):
    """
    Analyze a URL for trustworthiness and potential threats.
    
    Parameters:
    - url: The URL to analyze
    
    Returns:
    - URLAnalysisResponse containing the analysis results
    """
    try:
        return await app.state.url_analysis_service.analyze_url(request.url)
    except Exception as e:
        logging.error(f"Error analyzing URL: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Configure timeout settings
@app.middleware("http")
async def timeout_middleware(request, call_next):
    try:
        return await asyncio.wait_for(call_next(request), timeout=120)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timeout"}
        )

@app.on_event("startup")
async def startup_event():
    # Initialize persistent sessions on startup
    await app.state.news_service.get_session()
    await app.state.news_service.get_translate_session()

@app.on_event("shutdown")
async def shutdown_event():
    # Cleanup sessions on shutdown
    await app.state.news_service.cleanup()

if __name__ == "__main__":
    print("\n=== YEAH News Detection API ===")
    print("Starting server with CORS enabled...")
    print("Allowed origins: *")
    print("Allowed methods: GET, POST, PUT, DELETE, OPTIONS, HEAD")
    
    print(f"API endpoint: http://{host}:{port}")
    print("==============================\n")
    
    import uvicorn
    uvicorn.run(
        "main:app", 
        host=host, 
        port=port,
        reload=True,
        log_level="info",
        proxy_headers=True
    )
# The app object is imported by api/index.py for Vercel deployment