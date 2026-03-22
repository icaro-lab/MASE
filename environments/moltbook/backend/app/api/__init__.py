from fastapi import APIRouter
from app.api import agents, posts, comments, submolts, votes, feed, search

api_router = APIRouter()

api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(comments.router, prefix="", tags=["comments"])
api_router.include_router(submolts.router, prefix="/submolts", tags=["submolts"])
api_router.include_router(votes.router, prefix="", tags=["votes"])
api_router.include_router(feed.router, prefix="/feed", tags=["feed"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
