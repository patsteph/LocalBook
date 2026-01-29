"""RLM Executor - Recursive Language Model for Notebook-Scale Analysis

v1.1.0: Implements RLM patterns for processing 50+ documents that exceed
normal LLM context windows. Uses code execution to navigate large contexts.

Key Features:
- Root LLM (olmo-3:7b) orchestrates analysis via Python code
- Sub LLM (phi4-mini) analyzes individual chunks
- Integrates with job_queue for background processing
- Safe code execution with sandboxed namespace

Use Cases:
- "Compare methodologies across all papers"
- "Find contradictions between documents"
- "Timeline of events in this research collection"
"""

import re
import io
import sys
import json
import asyncio
from typing import Dict, Any, List, Optional, AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
import httpx

from config import settings
from storage.source_store import source_store


@dataclass
class RLMResult:
    """Result from RLM analysis."""
    answer: str
    sources_cited: List[str]
    iterations: int
    execution_log: List[str]
    total_time_seconds: float


class RLMExecutor:
    """
    Recursive Language Model executor for notebook-scale analysis.
    
    Uses code execution to navigate contexts larger than model windows.
    """
    
    def __init__(
        self,
        root_model: str = None,
        sub_model: str = None,
        max_iterations: int = 15,
        ollama_url: str = None
    ):
        self.root_model = root_model or settings.ollama_model
        self.sub_model = sub_model or settings.ollama_fast_model
        self.max_iterations = max_iterations
        self.ollama_url = ollama_url or settings.ollama_base_url
        
    async def analyze_notebook(
        self,
        notebook_id: str,
        query: str,
        progress_callback: Optional[callable] = None
    ) -> RLMResult:
        """
        Analyze entire notebook with RLM pattern.
        
        Args:
            notebook_id: Notebook to analyze
            query: User's question
            progress_callback: Optional callback for progress updates
            
        Returns:
            RLMResult with answer and metadata
        """
        import time
        start_time = time.time()
        execution_log = []
        
        def log(msg: str):
            execution_log.append(f"[{time.time() - start_time:.1f}s] {msg}")
            print(f"[RLM] {msg}")
            if progress_callback:
                try:
                    progress_callback(msg)
                except:
                    pass
        
        log(f"Starting RLM analysis for notebook {notebook_id}")
        
        # Step 1: Load all sources
        all_sources = self._load_notebook_sources(notebook_id)
        if not all_sources:
            return RLMResult(
                answer="No sources found in this notebook.",
                sources_cited=[],
                iterations=0,
                execution_log=execution_log,
                total_time_seconds=time.time() - start_time
            )
        
        log(f"Loaded {len(all_sources)} sources ({sum(len(s['content']) for s in all_sources):,} chars)")
        
        # Step 2: Build concatenated context
        full_context = "\n\n---DOC_BREAK---\n\n".join([
            f"[SOURCE: {s['title']}]\n{s['content']}"
            for s in all_sources
        ])
        
        # Step 3: Create execution namespace
        namespace = {
            'context': full_context,
            'sources': all_sources,
            'answer': None,
            'answer_ready': False,
            'cited_sources': [],
            'llm_query': lambda chunk, q: asyncio.get_event_loop().run_until_complete(
                self._sub_llm_call(chunk, q)
            ),
            're': re,
            'json': json,
        }
        
        # Step 4: Build system prompt
        system_prompt = self._build_system_prompt(
            doc_count=len(all_sources),
            total_chars=len(full_context)
        )
        
        # Step 5: Run RLM loop
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Query: {query}"}
        ]
        
        for iteration in range(self.max_iterations):
            log(f"Iteration {iteration + 1}/{self.max_iterations}")
            
            # Get root LLM response
            response = await self._call_root_llm(messages)
            messages.append({"role": "assistant", "content": response})
            
            # Extract code blocks
            code_blocks = re.findall(
                r'```python\n(.*?)```',
                response,
                re.DOTALL
            )
            
            if code_blocks:
                outputs = []
                for code in code_blocks:
                    log(f"Executing code block ({len(code)} chars)")
                    result = self._safe_execute(code, namespace)
                    outputs.append(result)
                    
                    # Check if done
                    if namespace.get('answer_ready') and namespace.get('answer'):
                        log("Answer ready - completing")
                        return RLMResult(
                            answer=namespace['answer'],
                            sources_cited=namespace.get('cited_sources', []),
                            iterations=iteration + 1,
                            execution_log=execution_log,
                            total_time_seconds=time.time() - start_time
                        )
                
                # Add execution results to conversation
                messages.append({
                    "role": "user",
                    "content": f"Execution output:\n{chr(10).join(outputs)}\n\nContinue analysis or set answer and answer_ready=True when done."
                })
            else:
                # No code blocks - prompt for code
                messages.append({
                    "role": "user",
                    "content": "Please write ```python code blocks to analyze the sources."
                })
        
        log(f"Max iterations reached")
        
        # Return partial answer if available
        final_answer = namespace.get('answer') or "Analysis incomplete - max iterations reached. Try a more specific query."
        
        return RLMResult(
            answer=final_answer,
            sources_cited=namespace.get('cited_sources', []),
            iterations=self.max_iterations,
            execution_log=execution_log,
            total_time_seconds=time.time() - start_time
        )
    
    def _load_notebook_sources(self, notebook_id: str) -> List[Dict]:
        """Load all sources for a notebook."""
        try:
            sources_data = source_store._load_data()
            notebook_sources = []
            
            for source_id, source in sources_data.get("sources", {}).items():
                if source.get("notebook_id") == notebook_id:
                    # Get content from chunks or raw content
                    content = source.get("content", "")
                    if not content:
                        # Try to get from chunks
                        chunks = source.get("chunks", [])
                        if chunks:
                            content = "\n\n".join(chunks)
                    
                    if content:
                        notebook_sources.append({
                            "id": source_id,
                            "title": source.get("title") or source.get("filename", "Untitled"),
                            "content": content[:50000],  # Limit per-source
                            "type": source.get("format", "unknown")
                        })
            
            return notebook_sources
        except Exception as e:
            print(f"[RLM] Error loading sources: {e}")
            return []
    
    def _build_system_prompt(self, doc_count: int, total_chars: int) -> str:
        """Build the RLM system prompt."""
        return f"""You are analyzing a notebook with {doc_count} documents ({total_chars:,} characters total).

You control a Python REPL environment. Available variables and functions:

- `context`: Full concatenated text of all documents (str)
- `sources`: List of dicts with 'id', 'title', 'content', 'type' for each document
- `llm_query(chunk, question)`: Call sub-LLM to analyze a text chunk (returns str)
- `re`: Python regex module
- `json`: Python json module
- `cited_sources`: List to append source IDs you reference
- `answer`: Set this to your final answer string
- `answer_ready`: Set to True when analysis is complete

STRATEGY:
1. First, explore the structure: print doc titles, sample content
2. Use Python (regex, string ops) for cheap exploration
3. Use llm_query() sparingly for semantic analysis of specific chunks
4. Build your answer incrementally
5. When done, set answer="your answer" and answer_ready=True

Write Python code in ```python blocks. I will execute and show output.

Example workflow:
```python
# Step 1: See what documents we have
for i, s in enumerate(sources[:10]):
    print(f"{{i+1}}. {{s['title']}} ({{len(s['content'])}} chars)")
```

[I'll execute and show output, then you continue...]

```python
# Step 2: Search for relevant content
matches = []
for s in sources:
    if re.search(r'methodology|method|approach', s['content'], re.I):
        matches.append(s['title'])
print(f"Found methodology in: {{matches}}")
```

Continue until you have enough information, then:
```python
answer = "Based on analysis of {doc_count} documents..."
cited_sources.extend(['source_id_1', 'source_id_2'])
answer_ready = True
```"""

    def _safe_execute(self, code: str, namespace: Dict) -> str:
        """Execute code safely and capture output."""
        # Capture stdout
        stdout = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout
        
        try:
            # Basic safety: no imports of dangerous modules
            forbidden = ['os', 'subprocess', 'shutil', 'pathlib', 'open', 'exec', 'eval']
            for f in forbidden:
                if f in code and f not in ['re.', 'json.']:
                    return f"Error: '{f}' is not allowed for safety reasons"
            
            exec(code, namespace)
            output = stdout.getvalue()
            return output if output else "[No output]"
            
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        finally:
            sys.stdout = old_stdout
    
    async def _call_root_llm(self, messages: List[Dict]) -> str:
        """Call root LLM for orchestration."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Convert to Ollama format
            prompt = "\n\n".join([
                f"{m['role'].upper()}: {m['content']}"
                for m in messages
            ])
            
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.root_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 2000,
                    }
                }
            )
            
            if response.status_code == 200:
                return response.json().get("response", "")
            return f"Error: {response.status_code}"
    
    async def _sub_llm_call(self, chunk: str, question: str) -> str:
        """Call sub-LLM for chunk analysis."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.sub_model,
                    "prompt": f"Context:\n{chunk[:3000]}\n\nQuestion: {question}\n\nAnswer concisely:",
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 500,
                    }
                }
            )
            
            if response.status_code == 200:
                return response.json().get("response", "")
            return f"Error: {response.status_code}"


# Integration with Job Queue
async def run_rlm_job(
    notebook_id: str,
    query: str,
    job_id: str,
    update_progress: callable
) -> Dict[str, Any]:
    """
    Run RLM analysis as a background job.
    
    This function is called by the job queue system.
    """
    executor = RLMExecutor()
    
    def progress_callback(msg: str):
        update_progress(message=msg)
    
    result = await executor.analyze_notebook(
        notebook_id=notebook_id,
        query=query,
        progress_callback=progress_callback
    )
    
    return {
        "answer": result.answer,
        "sources_cited": result.sources_cited,
        "iterations": result.iterations,
        "execution_log": result.execution_log,
        "total_time_seconds": result.total_time_seconds
    }


# Singleton instance
rlm_executor = RLMExecutor()
