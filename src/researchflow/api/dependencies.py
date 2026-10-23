"""FastAPI dependencies backed by application state."""

from fastapi import Request

from researchflow.core.config import Settings
from researchflow.db.database import Database
from researchflow.documents.service import DocumentService
from researchflow.indexing.service import IndexingService
from researchflow.rag.service import RAGService
from researchflow.retrieval.service import RetrievalService
from researchflow.services.chat import ChatService


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_document_service(request: Request) -> DocumentService:
    return request.app.state.document_service


def get_indexing_service(request: Request) -> IndexingService:
    return request.app.state.indexing_service


def get_retrieval_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval_service


def get_rag_service(request: Request) -> RAGService:
    return request.app.state.rag_service
