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

def _merge_adjacent(pieces: List[tuple]) -> List[str]:
    """Combine adjacent leaf chunks up to `chunk_size`, preserving document order.

    `pieces` is [(group_id, text), …] in document order, where group_id is the section.

    Why (2026-09-14): the old code emitted each leaf as its own chunk and DROPPED anything
    under 100 characters. On the test PDF that produced 20 chunks averaging 213 characters
    against a configured `chunk_size` of 1000 — so retrieval returned fragments with almost no
    surrounding context, the reranker had little to work with, and every fragment cost its own
    embedding. Worse, the 100-char floor silently discarded short sections: content that was in
    the document simply never reached the index.

    Merging preferentially within a section keeps a chunk about one topic. A run that is still
    tiny after that is merged across the boundary anyway — a 130-character chunk is worse for
    retrieval than a slightly mixed one, and far worse than the alternative of dropping it.
    """
    from config import settings

    target = getattr(settings, "chunk_size", 1000)
    floor = max(1, target // 4)

    out: List[str] = []
    buf: List[str] = []
    buf_group = None

    def _flush():
        if buf:
            out.append("\n\n".join(buf).strip())

    for group, body in pieces:
        body = (body or "").strip()
        if not body:
            continue
        if not buf:
            buf, buf_group = [body], group
            continue

        current_len = sum(len(b) for b in buf) + 2 * len(buf)
        same_section = group == buf_group
        would_fit = current_len + len(body) <= target

        # Same section and it fits → merge. Different section but the buffer is still below the
        # floor → merge anyway rather than emit a fragment. Otherwise start a new chunk.
        if would_fit and (same_section or current_len < floor):
            buf.append(body)
            if not same_section:
                buf_group = group
        else:
            _flush()
            buf, buf_group = [body], group

    _flush()
    out = [c for c in out if c]

    # Merging only ever looks forward, so a small final run has nothing to join. Fold a
    # sub-floor tail back into its predecessor rather than indexing a fragment.
    if len(out) > 1 and len(out[-1]) < floor and len(out[-2]) + len(out[-1]) <= target * 1.5:
        tail = out.pop()
        out[-1] = f"{out[-1]}\n\n{tail}"
    return out


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
        
        # LEAVES ONLY (2026-09-14). This used to emit every level-1 AND level-2 chunk, so a
        # section and its own paragraphs both went into the store: measured on the test PDF,
        # 15 of 16 paragraph chunks were contained verbatim in a section chunk — 4,239 chars
        # indexed from a 2,901-char document, 1.46× duplication. That cost ~2x the embedding
        # calls at ingest (the reported PDF slowness), and duplicate text competed for the five
        # retrieval slots, pushing genuinely different material out of the results.
        #
        # A section is a leaf only when it has no paragraph children; otherwise its children
        # represent it. `parent_id` is what makes that decidable.
        parented = {c.parent_id for c in hier_chunks if c.level == 2 and c.parent_id}
        leaves = [
            c for c in hier_chunks
            if c.level == 2 or (c.level == 1 and c.chunk_id not in parented)
        ]

        # A heading's own text is consumed into `section_title` and is NOT part of any node's
        # `.text`, so it never reaches the index. That is invisible for a heading like
        # "Overview", and lossy for this chunker's numbered-section pattern
        # (`^\d+\.\s+([A-Z].+)$`), which classifies the steps of a numbered LIST as headings.
        # Measured on the test PDF: "Embeds the query using the Snowflake Arctic Embed 2
        # model", "Searches the LanceDB vector store" and "Reranks results using cross-encoder
        # scoring" — the actual content of the RAG-pipeline list — were absent from the index
        # entirely, under the old code as well as the new. Emitting the heading ahead of its
        # children restores it and gives the children their context.
        # Heading text now lives in the section body itself (`hierarchical_chunker.
        # _detect_sections` keeps the heading line), so it reaches the index through normal
        # content and does NOT need to be re-emitted here. Re-emitting it as well pushed the
        # index to 1.65x the source — trading one kind of duplication for another.
        pieces = [
            (c.parent_id or c.chunk_id, c.text)
            for c in leaves
        ]

        result = _merge_adjacent(pieces)

        if result:
            print(f"[RAG] Hierarchical chunking: {len(result)} chunks "
                  f"({len(leaves)} leaves of {len(hier_chunks)} hierarchy nodes)")
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
