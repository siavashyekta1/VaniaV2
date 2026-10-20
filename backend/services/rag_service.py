import os
import re
import tempfile
import logging
from pathlib import Path
from django.conf import settings
from .models import KnowledgeDocument
from agno.media import File as AgnoFile

# --- Agno Imports ---
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.qdrant import Qdrant
from agno.knowledge.embedder.openai import OpenAIEmbedder
from agno.knowledge.reader.pdf_reader import PDFReader
from agno.knowledge.reader.text_reader import TextReader
from core.ai_provider import get_agno_openai_kwargs

logger = logging.getLogger(__name__)

def get_sanitized_table_name(name: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
    return f"kb_{clean}"


def get_session_knowledge_collection_name(session_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", session_id.lower())
    return f"session_{clean}"


def build_qdrant_knowledge(collection_name: str) -> Knowledge:
    vector_db = Qdrant(
        collection=collection_name,
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
        embedder=OpenAIEmbedder(id="text-embedding-3-small", **get_agno_openai_kwargs(for_embedder=True)),
    )
    return Knowledge(vector_db=vector_db)


def get_session_knowledge(session_id: str) -> Knowledge:
    return build_qdrant_knowledge(get_session_knowledge_collection_name(session_id))


def session_knowledge_exists(session_id: str) -> bool:
    vector_db = Qdrant(
        collection=get_session_knowledge_collection_name(session_id),
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
        embedder=OpenAIEmbedder(id="text-embedding-3-small", **get_agno_openai_kwargs(for_embedder=True)),
    )
    if not vector_db.exists():
        return False
    return vector_db.get_count() > 0


def ingest_session_file(session_id: str, file: AgnoFile, attachment_id: str | None = None) -> bool:
    file_name = getattr(file, "name", None) or getattr(file, "path", None) or "uploaded_file"
    file_bytes = getattr(file, "content", None)
    if not file_bytes:
        logger.warning("Uploaded session file '%s' has no content.", file_name)
        return False

    ext = Path(str(file_name)).suffix.lower()
    if ext != ".pdf":
        logger.info("Skipping session knowledge ingestion for unsupported file type: %s", file_name)
        return False

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".bin")
    temp_path = Path(temp_file.name)
    try:
        temp_file.write(file_bytes)
        temp_file.close()

        knowledge = get_session_knowledge(session_id)
        knowledge.add_content(
            path=str(temp_path),
            metadata={
                "session_id": session_id,
                "source": "uploaded_attachment",
                "filename": str(file_name),
                **({"attachment_id": attachment_id} if attachment_id else {}),
            },
            reader=PDFReader(),
            upsert=True,
            skip_if_exists=True,
        )
        return True
    except Exception as e:
        logger.error("Failed ingesting session file '%s': %s", file_name, e, exc_info=True)
        return False
    finally:
        try:
            temp_file.close()
        except Exception:
            pass
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as e:
                logger.warning("Failed to delete session temp file '%s': %s", temp_path, e)


def search_session_knowledge(session_id: str, query: str, max_results: int = 5) -> list:
    knowledge = get_session_knowledge(session_id)
    try:
        docs = knowledge.search(
            query=query,
            max_results=max_results,
            filters={"session_id": session_id, "source": "uploaded_attachment"},
        )
        return docs or []
    except Exception as e:
        logger.error("Failed searching session knowledge for '%s': %s", session_id, e, exc_info=True)
        return []


def remove_session_file(session_id: str, attachment_id: str) -> bool:
    knowledge = get_session_knowledge(session_id)
    try:
        return knowledge.remove_vectors_by_metadata(
            {
                "session_id": session_id,
                "source": "uploaded_attachment",
                "attachment_id": attachment_id,
            }
        )
    except Exception as e:
        logger.error(
            "Failed removing session attachment '%s' for '%s': %s",
            attachment_id,
            session_id,
            e,
            exc_info=True,
        )
        return False


def render_session_knowledge_context(session_id: str, query: str, max_results: int = 5) -> str:
    docs = search_session_knowledge(session_id, query, max_results=max_results)
    if not docs:
        return ""

    rendered = []
    for doc in docs:
        meta = getattr(doc, "meta_data", {}) or {}
        filename = meta.get("filename") or getattr(doc, "name", None) or "attached_file"
        page = meta.get("page")
        label = f"{filename} (page {page})" if page is not None else str(filename)
        content = (getattr(doc, "content", "") or "").strip()
        if not content:
            continue
        rendered.append(f"[{label}]\n{content}")

    if not rendered:
        return ""

    return (
        "<attached_file_context>\n"
        "Use the following retrieved excerpts from files uploaded in this thread when answering.\n\n"
        f"{'\n\n'.join(rendered)}\n"
        "</attached_file_context>"
    )

class RAGIngestionService:
    
    @staticmethod
    def process_document(document_id: int):
        try:
            doc = KnowledgeDocument.objects.get(id=document_id)
        except KnowledgeDocument.DoesNotExist:
            logger.error(f"❌ [RAG] Document ID {document_id} not found.")
            return

        logger.info(f"🚀 [RAG] Starting ingestion for: {doc.file.name}")
        
        doc.status = KnowledgeDocument.Status.PROCESSING
        doc.save(update_fields=['status'])

        source_path = None
        is_temp_file = False

        try:
            # 1. Resolve File Path (Local vs S3/MinIO)
            try:
                # Attempt to get local filesystem path
                source_path = Path(doc.file.path).resolve()
            except NotImplementedError:
                # File is on S3/MinIO
                logger.info(f"   -> Downloading from storage: {doc.file.url}")
                
                # Get extension
                fname = doc.file.name.split('/')[-1]
                ext = Path(fname).suffix or ".txt"
                
                # Create a temp file
                # delete=False is required so we can close it and let Agno open it by path
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                
                # Download content properly
                # If using signed URLs (AWS_QUERYSTRING_AUTH=True), use requests
                # If internal, we can also use doc.file.open() to be storage-agnostic
                with doc.file.open('rb') as f:
                    temp_file.write(f.read())
                
                temp_file.close()
                source_path = Path(temp_file.name)
                is_temp_file = True
            
            # 2. Select Reader
            ext = source_path.suffix.lower()
            reader = None
            if ext == '.pdf':
                reader = PDFReader(chunk=True)
            elif ext in ['.txt', '.md', '.csv', '.json', '.py']:
                reader = TextReader(chunk=True)
            else:
                # Fallback for unknown text files
                reader = TextReader(chunk=True)

            # 3. Setup Vector Database
            collection_name = get_sanitized_table_name(doc.knowledge_base.name)
            
            vector_db = Qdrant(
                collection=collection_name,
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
                embedder=OpenAIEmbedder(id="text-embedding-3-small", **get_agno_openai_kwargs(for_embedder=True)),
            )

            # 4. Initialize Knowledge
            # We don't pass 'storage', so Agno will warn "Contents DB not found".
            # This is expected and harmless as Django tracks the document state.
            knowledge = Knowledge(vector_db=vector_db)

            # 5. Ingest
            logger.info(f"   -> Chunking & Embedding into '{collection_name}'...")
            knowledge.add_content(
                path=source_path, 
                reader=reader
            )

            # 6. Success
            doc.status = KnowledgeDocument.Status.COMPLETED
            doc.error_message = ""
            doc.save(update_fields=['status', 'error_message'])
            logger.info(f"✅ [RAG] Ingestion Complete for: {doc.file.name}")

        except Exception as e:
            logger.error(f"❌ [RAG] Ingestion Failed: {e}", exc_info=True)
            doc.status = KnowledgeDocument.Status.FAILED
            doc.error_message = str(e)
            doc.save(update_fields=['status', 'error_message'])
        
        finally:
            # 7. Guaranteed Cleanup
            # This runs whether the task succeeds OR fails
            if is_temp_file and source_path and os.path.exists(source_path):
                try:
                    os.remove(source_path)
                    logger.info("   -> Temp file cleaned up.")
                except OSError as e:
                    logger.warning(f"   -> Failed to delete temp file: {e}")
