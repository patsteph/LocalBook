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
        timeout: float = 300.0,
        num_predict: Optional[int] = None,
        extra_options: Optional[Dict[str, Any]] = None,
        images: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Generate a response from Ollama.
        
        Args:
            prompt: The user prompt
            model: Model to use (defaults to settings.ollama_model)
            system: System prompt (optional)
            temperature: Sampling temperature
            timeout: Request timeout in seconds
            num_predict: Max tokens to generate (optional)
            extra_options: Additional Ollama options (optional)
            
        Returns:
            Dict with 'response' key containing the generated text
        """
        model = model or settings.ollama_model
        
        options: Dict[str, Any] = {
            "temperature": temperature
        }
        if num_predict is not None:
            options["num_predict"] = num_predict
        if extra_options:
            options.update(extra_options)
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        
        if system:
            payload["system"] = system
        
        # Images are a top-level field for /api/generate (LLaVA/Granite style)
        if images:
            payload["images"] = images
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=timeout)) as client:
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
        timeout: float = 300.0,
        images: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Chat completion with Ollama.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            temperature: Sampling temperature
            timeout: Request timeout in seconds
            images: Optional list of base64-encoded image strings (injected into last user message)
            
        Returns:
            Dict with 'message' key containing the response
        """
        model = model or settings.ollama_model
        
        # If images are provided, inject them into the last user message
        if images:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    msg["images"] = images
                    break
        
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

    async def vision_describe(
        self,
        image_b64: str,
        prompt: str,
        model: Optional[str] = None,
        api_style: str = "generate",
        timeout: float = 90.0,
        num_predict: int = 400,
    ) -> str:
        """
        Universal vision dispatcher — routes to /api/generate or /api/chat
        depending on which API style the vision model requires.
        
        Args:
            image_b64: Base64-encoded image data
            prompt: Text prompt to describe the image
            model: Vision model to use (defaults to settings.vision_model)
            api_style: "generate" for LLaVA/Granite, "chat" for Gemma4/Llama3.2
            timeout: Request timeout
            num_predict: Max tokens for the response
            
        Returns:
            The model's text description of the image
        """
        model = model or settings.vision_model
        
        try:
            if api_style == "chat":
                # Gemma 4 / Llama 3.2 style — images go inside chat messages
                result = await self.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.3,
                    timeout=timeout,
                    images=[image_b64],
                )
                return result.get("message", {}).get("content", "")
            else:
                # Granite / LLaVA style — images are top-level in /api/generate
                result = await self.generate(
                    prompt=prompt,
                    model=model,
                    temperature=0.3,
                    timeout=timeout,
                    num_predict=num_predict,
                    images=[image_b64],
                )
                return result.get("response", "")
        except Exception as e:
            logger.error(f"Vision describe failed: {e}")
            return f"Error: {str(e)}"


# Singleton instance
ollama_client = OllamaClient()

