"""
Ollama Client - Shared LLM client for agents

Provides a simple interface for making Ollama API calls across the codebase.
"""
import httpx
import logging
from typing import Optional, Dict, Any

from config import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Simple async client for Ollama API calls"""
    
    def __init__(self):
        self.base_url = settings.ollama_base_url.rstrip('/')
    
    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        timeout: float = 300.0
    ) -> Dict[str, Any]:
        """
        Generate a response from Ollama.
        
        Args:
            prompt: The user prompt
            model: Model to use (defaults to settings.ollama_model)
            system: System prompt (optional)
            temperature: Sampling temperature
            timeout: Request timeout in seconds
            
        Returns:
            Dict with 'response' key containing the generated text
        """
        model = model or settings.ollama_model
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        
        if system:
            payload["system"] = system
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout)) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            logger.error(f"Ollama request timed out after {timeout}s")
            return {"response": "Request timed out"}
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            return {"response": f"Error: {str(e)}"}
    
    async def chat(
        self,
        messages: list,
        model: Optional[str] = None,
        temperature: float = 0.7,
        timeout: float = 300.0
    ) -> Dict[str, Any]:
        """
        Chat completion with Ollama.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            temperature: Sampling temperature
            timeout: Request timeout in seconds
            
        Returns:
            Dict with 'message' key containing the response
        """
        model = model or settings.ollama_model
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout)) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Ollama chat request failed: {e}")
            return {"message": {"content": f"Error: {str(e)}"}}


# Singleton instance
ollama_client = OllamaClient()
