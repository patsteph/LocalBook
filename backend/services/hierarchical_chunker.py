"""Hierarchical Chunking Service

Creates chunks at multiple levels (document, section, paragraph) for better retrieval.
Enables retrieval at different granularities based on query type.

Based on LlamaIndex and LangChain hierarchical retrieval patterns.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class HierarchicalChunk:
    """A chunk with hierarchy information."""
    text: str
    level: int  # 0=document summary, 1=section, 2=paragraph, 3=sentence
    level_name: str  # "document", "section", "paragraph", "sentence"
    parent_id: Optional[str] = None
    chunk_id: str = ""
    section_title: str = ""
    position: int = 0  # Position within parent
    metadata: Dict = field(default_factory=dict)


class HierarchicalChunker:
    """Creates hierarchical chunks from documents."""
    
    def __init__(self):
        # Chunk size targets for each level
        self.chunk_sizes = {
            "document": 4000,   # Document summary
            "section": 1500,    # Section chunks
            "paragraph": 500,   # Paragraph chunks
            "sentence": 150,    # Sentence chunks (for precise retrieval)
        }
        
        # Section header patterns
        self.section_patterns = [
            r'^#{1,3}\s+(.+)$',  # Markdown headers
            r'^([A-Z][A-Za-z\s]+):$',  # Title: format
            r'^\d+\.\s+([A-Z].+)$',  # Numbered sections
            r'^===\s*Page\s+\d+\s*===$',  # PDF page markers
            r'^[A-Z][A-Z\s]{5,50}$',  # ALL CAPS headers
        ]
    
    def _detect_sections(self, text: str) -> List[Tuple[str, str]]:
        """Detect section boundaries in text.
        
        Returns: List of (section_title, section_content) tuples
        """
        lines = text.split('\n')
        sections = []
        current_title = "Introduction"
        current_content = []
        
        for line in lines:
            is_header = False
            header_text = None
            
            for pattern in self.section_patterns:
                match = re.match(pattern, line.strip())
                if match:
                    is_header = True
                    header_text = match.group(1) if match.lastindex else line.strip()
                    break
            
            if is_header and header_text:
                # Save previous section
                if current_content:
                    sections.append((current_title, '\n'.join(current_content)))
                current_title = header_text
                current_content = []
            else:
                current_content.append(line)
        
        # Don't forget the last section
        if current_content:
            sections.append((current_title, '\n'.join(current_content)))
        
        return sections
    
    def _split_into_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs."""
        # Split on double newlines or single newlines followed by indent
        paragraphs = re.split(r'\n\s*\n|\n(?=\s{2,})', text)
        
        # Clean and filter
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        return paragraphs
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        # Simple sentence splitting
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
        
        # Clean and filter
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        
        return sentences
    
    def _merge_small_chunks(
        self,
        chunks: List[str],
        min_size: int,
        max_size: int
    ) -> List[str]:
        """Merge chunks that are too small."""
        if not chunks:
            return []
        
        merged = []
        current = chunks[0]
        
        for chunk in chunks[1:]:
            if len(current) < min_size and len(current) + len(chunk) < max_size:
                current = current + "\n\n" + chunk
            else:
                if current.strip():
                    merged.append(current.strip())
                current = chunk
        
        if current.strip():
            merged.append(current.strip())
        
        return merged
    
    def chunk_document(
        self,
        text: str,
        source_id: str,
        filename: str = "",
        include_sentences: bool = False
    ) -> List[HierarchicalChunk]:
        """Create hierarchical chunks from a document.
        
        Args:
            text: Document text
            source_id: Source ID for chunk IDs
            filename: Optional filename for context
            include_sentences: Whether to include sentence-level chunks
            
        Returns: List of hierarchical chunks
        """
        chunks = []
        
        # Level 0: Document summary (first ~4000 chars or whole doc if smaller)
        doc_summary = text[:self.chunk_sizes["document"]]
        if len(text) > self.chunk_sizes["document"]:
            doc_summary += "..."
        
        doc_chunk = HierarchicalChunk(
            text=doc_summary,
            level=0,
            level_name="document",
            chunk_id=f"{source_id}_doc",
            section_title=filename or "Document Overview",
            position=0,
            metadata={"is_summary": True}
        )
        chunks.append(doc_chunk)
        
        # Level 1: Sections
        sections = self._detect_sections(text)
        
        for section_idx, (section_title, section_content) in enumerate(sections):
            if not section_content.strip():
                continue
            
            section_chunk_id = f"{source_id}_s{section_idx}"
            
            # Create section chunk
            section_text = section_content[:self.chunk_sizes["section"]]
            section_chunk = HierarchicalChunk(
                text=section_text,
                level=1,
                level_name="section",
                parent_id=doc_chunk.chunk_id,
                chunk_id=section_chunk_id,
                section_title=section_title,
                position=section_idx,
                metadata={"section_number": section_idx}
            )
            chunks.append(section_chunk)
            
            # Level 2: Paragraphs within section
            paragraphs = self._split_into_paragraphs(section_content)
            paragraphs = self._merge_small_chunks(
                paragraphs,
                min_size=100,
                max_size=self.chunk_sizes["paragraph"]
            )
            
            for para_idx, para_text in enumerate(paragraphs):
                if len(para_text) < 50:
                    continue
                
                para_chunk = HierarchicalChunk(
                    text=para_text,
                    level=2,
                    level_name="paragraph",
                    parent_id=section_chunk_id,
                    chunk_id=f"{source_id}_s{section_idx}_p{para_idx}",
                    section_title=section_title,
                    position=para_idx,
                    metadata={"paragraph_number": para_idx}
                )
                chunks.append(para_chunk)
                
                # Level 3: Sentences (optional, for very precise retrieval)
                if include_sentences and len(para_text) > 200:
                    sentences = self._split_into_sentences(para_text)
                    
                    for sent_idx, sent_text in enumerate(sentences):
                        if len(sent_text) < 30:
                            continue
                        
                        sent_chunk = HierarchicalChunk(
                            text=sent_text,
                            level=3,
                            level_name="sentence",
                            parent_id=para_chunk.chunk_id,
                            chunk_id=f"{source_id}_s{section_idx}_p{para_idx}_t{sent_idx}",
                            section_title=section_title,
                            position=sent_idx,
                            metadata={"sentence_number": sent_idx}
                        )
                        chunks.append(sent_chunk)
        
        print(f"[HierarchicalChunker] Created {len(chunks)} chunks at multiple levels")
        return chunks
    
    def get_chunks_for_level(
        self,
        chunks: List[HierarchicalChunk],
        level: int
    ) -> List[HierarchicalChunk]:
        """Get chunks at a specific level."""
        return [c for c in chunks if c.level == level]
    
    def get_parent_context(
        self,
        chunks: List[HierarchicalChunk],
        chunk_id: str
    ) -> str:
        """Get parent context for a chunk (for expanded retrieval)."""
        # Find the chunk
        chunk = next((c for c in chunks if c.chunk_id == chunk_id), None)
        if not chunk or not chunk.parent_id:
            return ""
        
        # Find parent
        parent = next((c for c in chunks if c.chunk_id == chunk.parent_id), None)
        if parent:
            return parent.text
        
        return ""
    
    def select_level_for_query(self, query: str) -> int:
        """Select appropriate chunk level based on query type.
        
        Returns: Level (0=doc, 1=section, 2=paragraph, 3=sentence)
        """
        query_lower = query.lower()
        
        # Overview queries -> document level
        if any(word in query_lower for word in ["overview", "summary", "about", "main", "topic"]):
            return 0
        
        # Specific fact queries -> paragraph or sentence level
        if any(word in query_lower for word in ["exactly", "specific", "precisely", "what is the"]):
            return 2
        
        # Most queries work well at section level
        return 1
    
    def format_for_indexing(self, chunks: List[HierarchicalChunk]) -> List[Dict]:
        """Format chunks for vector DB indexing.
        
        Returns list of dicts with text and metadata.
        """
        formatted = []
        
        for chunk in chunks:
            formatted.append({
                "text": chunk.text,
                "chunk_id": chunk.chunk_id,
                "level": chunk.level,
                "level_name": chunk.level_name,
                "parent_id": chunk.parent_id,
                "section_title": chunk.section_title,
                "position": chunk.position,
                **chunk.metadata
            })
        
        return formatted


# Singleton instance
hierarchical_chunker = HierarchicalChunker()
