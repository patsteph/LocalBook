"""
RAG Chunking — Text chunking strategies for document ingestion.

Extracted from rag_engine.py Phase 4a. Functions take explicit parameters
instead of relying on instance state. RAGEngine delegates to these.
"""
import re
from typing import List

from config import settings


# ─── Smart Chunking Router ──────────────────────────────────────────────────────

def chunk_text_smart(text: str, source_type: str, filename: str) -> List[str]:
    """Smart chunking that adapts strategy based on source type.
    
    Different file types need different chunking strategies:
    - Tabular data (xlsx, csv): Keep rows together, include headers in each chunk
    - Documents (pdf, docx): Hierarchical chunking by sections/paragraphs
    - Code: Split by functions/classes
    - Transcripts: Split by speaker turns or time segments
    """
    filename_lower = filename.lower()
    
    # Detect tabular data
    is_tabular = source_type in ['xlsx', 'xls', 'csv'] or \
                 filename_lower.endswith(('.xlsx', '.xls', '.csv'))
    
    # Detect if content looks like tabular data (row-based format)
    if not is_tabular and 'Row ' in text[:500] and ': ' in text[:500]:
        is_tabular = True
    
    if is_tabular:
        return chunk_tabular_data(text)
    
    # Use hierarchical chunking for structured documents (PDFs, docx)
    is_structured_doc = source_type in ['pdf', 'docx', 'doc', 'pptx'] or \
                       filename_lower.endswith(('.pdf', '.docx', '.doc', '.pptx'))
    
    if is_structured_doc and len(text) > 2000:
        return chunk_hierarchical(text, filename)
    
    # Default: use standard semantic chunking
    return chunk_text(text)


# ─── Hierarchical Chunking ──────────────────────────────────────────────────────

def chunk_hierarchical(text: str, filename: str) -> List[str]:
    """Hierarchical chunking for structured documents.
    
    Creates chunks at section and paragraph levels while preserving
    document structure. Each chunk includes section context for better retrieval.
    """
    try:
        from services.hierarchical_chunker import HierarchicalChunker
        
        chunker = HierarchicalChunker()
        hier_chunks = chunker.chunk_document(
            text=text,
            source_id="temp",
            filename=filename,
            include_sentences=False
        )
        
        result = []
        for chunk in hier_chunks:
            if chunk.level in [1, 2]:
                if chunk.section_title and chunk.level == 2:
                    chunk_text = f"[{chunk.section_title}]\n{chunk.text}"
                else:
                    chunk_text = chunk.text
                
                if len(chunk_text) >= 100:
                    result.append(chunk_text)
        
        if result:
            print(f"[RAG] Hierarchical chunking: {len(result)} chunks from {len(hier_chunks)} total levels")
            return result
        
    except Exception as e:
        print(f"[RAG] Hierarchical chunking failed, falling back to standard: {e}")
    
    # Fallback to standard chunking
    return chunk_text(text)


# ─── Tabular Chunking ───────────────────────────────────────────────────────────

def chunk_tabular_data(text: str) -> List[str]:
    """Chunk tabular data keeping related rows together with context.
    
    Strategy:
    1. Extract header/context lines (sheet name, column headers)
    2. Group rows into chunks respecting both row count AND character limits
    3. Prepend header context to each chunk for self-contained retrieval
    """
    max_chunk_chars = settings.chunk_size
    
    lines = text.split('\n')
    
    header_lines = []
    data_lines = []
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        if line_stripped.startswith('===') or \
           line_stripped.startswith('Data from sheet') or \
           line_stripped.startswith('Complete row data') or \
           line_stripped.startswith('This data is from') or \
           ('Column' in line_stripped and ':' in line_stripped and line_stripped.startswith('Row 1:')):
            header_lines.append(line_stripped)
        else:
            data_lines.append(line_stripped)
    
    header_context = '\n'.join(header_lines[:5]) if header_lines else ""
    header_len = len(header_context) + 2
    
    chunks = []
    current_chunk_lines = []
    current_chunk_len = header_len
    
    for line in data_lines:
        line_len = len(line) + 1
        
        if current_chunk_len + line_len > max_chunk_chars and current_chunk_lines:
            if header_context:
                chunk_text = header_context + '\n\n' + '\n'.join(current_chunk_lines)
            else:
                chunk_text = '\n'.join(current_chunk_lines)
            chunks.append(chunk_text)
            
            current_chunk_lines = [line]
            current_chunk_len = header_len + line_len
        else:
            current_chunk_lines.append(line)
            current_chunk_len += line_len
    
    if current_chunk_lines:
        if header_context:
            chunk_text = header_context + '\n\n' + '\n'.join(current_chunk_lines)
        else:
            chunk_text = '\n'.join(current_chunk_lines)
        if chunk_text.strip():
            chunks.append(chunk_text)
    
    if not chunks:
        return chunk_text_fallback(text)
    
    print(f"[RAG] Tabular chunking: {len(data_lines)} rows -> {len(chunks)} chunks (max {max_chunk_chars} chars/chunk)")
    return chunks


# ─── Standard Semantic Chunking ──────────────────────────────────────────────────

def chunk_text(text: str) -> List[str]:
    """Chunk text into smaller pieces with semantic boundary awareness.
    
    Tries to split at paragraph/sentence boundaries rather than mid-sentence
    for better embedding quality. Falls back to character-based splitting.
    """
    chunk_size = settings.chunk_size
    chunk_overlap = settings.chunk_overlap

    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            if len(para) > chunk_size:
                sentences = split_into_sentences(para)
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 > chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        if len(sentence) > chunk_size:
                            chunks.extend(char_split(sentence, chunk_size, chunk_overlap))
                            current_chunk = ""
                        else:
                            current_chunk = sentence
                    else:
                        current_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence
            else:
                current_chunk = para
        else:
            current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    if not chunks:
        return []
    
    if chunk_overlap > 0 and len(chunks) > 1:
        overlapped_chunks = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_end = chunks[i-1][-chunk_overlap:] if len(chunks[i-1]) > chunk_overlap else chunks[i-1]
            overlapped_chunks.append(prev_end + "\n" + chunks[i])
        chunks = overlapped_chunks
    
    return chunks


# Alias for tabular fallback (avoids circular call)
def chunk_text_fallback(text: str) -> List[str]:
    """Fallback chunking used when tabular chunking produces no results."""
    return chunk_text(text)


# ─── Helpers ─────────────────────────────────────────────────────────────────────

def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def char_split(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Fallback character-based splitting for very long text without boundaries."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def get_parent_context(chunks: List[str], chunk_index: int, max_parent_chars: int = 2000) -> str:
    """Get expanded parent context for a chunk.
    
    Combines the current chunk with surrounding chunks to provide
    more context during retrieval.
    """
    if not chunks or chunk_index < 0 or chunk_index >= len(chunks):
        return ""
    
    current_chunk = chunks[chunk_index]
    
    parent_parts = [current_chunk]
    current_len = len(current_chunk)
    
    # Add previous chunks
    prev_idx = chunk_index - 1
    while prev_idx >= 0 and current_len < max_parent_chars:
        prev_chunk = chunks[prev_idx]
        if current_len + len(prev_chunk) > max_parent_chars:
            remaining = max_parent_chars - current_len
            parent_parts.insert(0, prev_chunk[-remaining:] + "...")
            break
        parent_parts.insert(0, prev_chunk)
        current_len += len(prev_chunk)
        prev_idx -= 1
    
    # Add next chunks
    next_idx = chunk_index + 1
    while next_idx < len(chunks) and current_len < max_parent_chars:
        next_chunk = chunks[next_idx]
        if current_len + len(next_chunk) > max_parent_chars:
            remaining = max_parent_chars - current_len
            parent_parts.append("..." + next_chunk[:remaining])
            break
        parent_parts.append(next_chunk)
        current_len += len(next_chunk)
        next_idx += 1
    
    return "\n\n".join(parent_parts)
