"""Multimodal Extraction Service

Extracts images and charts from PDFs and generates text descriptions
for indexing in the RAG system.

Uses PyMuPDF for image extraction and granite3.2-vision for description generation.
Supports parallel processing for large documents with many images.

v1.0.5: Added parallel processing, granite3.2-vision model, background task support.
"""
import asyncio
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import httpx

from config import settings


@dataclass
class ExtractedImage:
    """An image extracted from a document."""
    image_bytes: bytes
    page_number: int
    image_index: int
    width: int
    height: int
    format: str  # png, jpeg, etc.
    description: str = ""  # LLM-generated description
    is_chart: bool = False  # Whether this appears to be a chart/graph


# Vision model for image description - granite3.2-vision is optimized for documents/charts
VISION_MODEL = "granite3.2-vision:2b"


class MultimodalExtractor:
    """Extracts and describes images from documents with parallel processing."""
    
    def __init__(self):
        self.min_image_size = 100  # Minimum dimension to extract
        self.max_images_per_doc = 100  # Increased for large PDFs
        self.max_parallel_workers = 4  # Concurrent vision model calls
        self.vision_model = VISION_MODEL
        self.image_cache_dir = Path(settings.db_path).parent / "images"
        self.image_cache_dir.mkdir(exist_ok=True)
        self._semaphore = None  # Initialized lazily for parallel processing
    
    def _is_meaningful_image(self, width: int, height: int) -> bool:
        """Check if image is large enough to be meaningful."""
        # Skip tiny images (icons, bullets, etc.)
        if width < self.min_image_size or height < self.min_image_size:
            return False
        # Skip very narrow images (likely decorative lines)
        aspect = max(width, height) / min(width, height)
        if aspect > 10:
            return False
        return True
    
    def _detect_chart_heuristic(self, image_bytes: bytes) -> bool:
        """Simple heuristic to detect if image might be a chart/graph.
        
        Charts typically have:
        - Limited color palette
        - Geometric shapes
        - Grid-like patterns
        """
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(image_bytes))
            
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Sample colors
            img_small = img.resize((50, 50))
            colors = img_small.getcolors(500)
            
            if colors:
                # Charts usually have fewer unique colors
                unique_colors = len(colors)
                if 5 < unique_colors < 50:
                    return True
            
            return False
        except Exception:
            return False
    
    async def extract_images_from_pdf(
        self,
        pdf_content: bytes,
        source_id: str
    ) -> List[ExtractedImage]:
        """Extract images from a PDF document.
        
        Args:
            pdf_content: Raw PDF bytes
            source_id: Source ID for caching
            
        Returns: List of extracted images
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            print("[MultimodalExtractor] PyMuPDF not installed, skipping image extraction")
            return []
        
        images = []
        
        try:
            doc = fitz.open(stream=pdf_content, filetype="pdf")
            image_count = 0
            
            for page_num, page in enumerate(doc, 1):
                if image_count >= self.max_images_per_doc:
                    break
                
                # Get images on this page
                image_list = page.get_images()
                
                for img_index, img_info in enumerate(image_list):
                    if image_count >= self.max_images_per_doc:
                        break
                    
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        
                        if not base_image:
                            continue
                        
                        image_bytes = base_image["image"]
                        width = base_image.get("width", 0)
                        height = base_image.get("height", 0)
                        img_format = base_image.get("ext", "png")
                        
                        # Skip small/decorative images
                        if not self._is_meaningful_image(width, height):
                            continue
                        
                        # Detect if it's likely a chart
                        is_chart = self._detect_chart_heuristic(image_bytes)
                        
                        extracted = ExtractedImage(
                            image_bytes=image_bytes,
                            page_number=page_num,
                            image_index=img_index,
                            width=width,
                            height=height,
                            format=img_format,
                            is_chart=is_chart
                        )
                        
                        images.append(extracted)
                        image_count += 1
                        
                    except Exception as e:
                        print(f"[MultimodalExtractor] Error extracting image {img_index} from page {page_num}: {e}")
                        continue
            
            doc.close()
            
            print(f"[MultimodalExtractor] Extracted {len(images)} images from PDF")
            return images
            
        except Exception as e:
            print(f"[MultimodalExtractor] Failed to extract images from PDF: {e}")
            return []
    
    async def describe_image(
        self,
        image: ExtractedImage,
        context: str = ""
    ) -> str:
        """Generate a text description of an image using vision LLM.
        
        Args:
            image: The extracted image
            context: Optional context about the document
            
        Returns: Text description of the image
        """
        # Convert to base64
        image_b64 = base64.b64encode(image.image_bytes).decode('utf-8')
        
        # Determine prompt based on image type
        if image.is_chart:
            prompt = """Describe this chart or graph in detail:
1. What type of chart is it (bar, line, pie, etc.)?
2. What data is being shown?
3. What are the key values or trends?
4. What conclusions can be drawn?

Be specific about numbers and labels visible in the chart."""
        else:
            prompt = """Describe this image in detail:
1. What is shown in the image?
2. What key information does it convey?
3. Are there any text labels, captions, or annotations?

Focus on information that would be useful for answering questions about this document."""
        
        if context:
            prompt = f"Context: This image is from a document about {context}\n\n{prompt}"
        
        try:
            # Use granite3.2-vision - optimized for document/chart understanding
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": self.vision_model,
                        "prompt": prompt,
                        "images": [image_b64],
                        "stream": False,
                        "options": {"num_predict": 400}
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    description = result.get("response", "").strip()
                    if description:
                        return description
                
                # Fallback: generate basic description without vision
                return self._generate_basic_description(image)
                
        except Exception as e:
            print(f"[MultimodalExtractor] Vision description failed: {e}")
            return self._generate_basic_description(image)
    
    def _generate_basic_description(self, image: ExtractedImage) -> str:
        """Generate a basic description without vision LLM."""
        if image.is_chart:
            return f"[Chart/Graph on page {image.page_number}] - Visual data representation ({image.width}x{image.height})"
        else:
            return f"[Image on page {image.page_number}] - Visual content ({image.width}x{image.height})"
    
    async def _describe_with_semaphore(
        self,
        image: ExtractedImage,
        context: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[Dict]:
        """Describe a single image with semaphore for rate limiting."""
        async with semaphore:
            description = await self.describe_image(image, context=context)
            if description:
                return {
                    "page": image.page_number,
                    "description": description,
                    "is_chart": image.is_chart,
                    "size": f"{image.width}x{image.height}"
                }
            return None
    
    async def extract_and_describe(
        self,
        pdf_content: bytes,
        source_id: str,
        filename: str = ""
    ) -> List[Dict]:
        """Extract images from PDF and generate descriptions with parallel processing.
        
        Returns list of {page, description, is_chart} for indexing.
        Uses parallel workers to speed up processing of large documents.
        """
        images = await self.extract_images_from_pdf(pdf_content, source_id)
        
        if not images:
            return []
        
        # Prioritize charts/graphs over regular images
        charts = [img for img in images if img.is_chart]
        regular = [img for img in images if not img.is_chart]
        prioritized = charts + regular
        
        # Limit to max_images_per_doc
        to_process = prioritized[:self.max_images_per_doc]
        
        print(f"[MultimodalExtractor] Processing {len(to_process)} images ({len(charts)} charts) with {self.max_parallel_workers} workers")
        
        # Create semaphore for parallel processing
        semaphore = asyncio.Semaphore(self.max_parallel_workers)
        
        # Process all images in parallel with rate limiting
        tasks = [
            self._describe_with_semaphore(image, filename, semaphore)
            for image in to_process
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out None results and exceptions
        valid_results = [
            r for r in results 
            if r is not None and not isinstance(r, Exception)
        ]
        
        print(f"[MultimodalExtractor] Generated {len(valid_results)} image descriptions")
        return valid_results
    
    def save_image_cache(
        self,
        image: ExtractedImage,
        source_id: str
    ) -> Optional[str]:
        """Save image to cache directory.
        
        Returns path to saved image.
        """
        try:
            filename = f"{source_id}_p{image.page_number}_i{image.image_index}.{image.format}"
            filepath = self.image_cache_dir / filename
            
            with open(filepath, 'wb') as f:
                f.write(image.image_bytes)
            
            return str(filepath)
        except Exception as e:
            print(f"[MultimodalExtractor] Failed to cache image: {e}")
            return None
    
    def format_for_indexing(self, image_descriptions: List[Dict]) -> str:
        """Format image descriptions for inclusion in document text.
        
        This text will be chunked and indexed for RAG retrieval.
        """
        if not image_descriptions:
            return ""
        
        parts = ["\n\n=== VISUAL CONTENT ===\n"]
        
        for desc in image_descriptions:
            chart_label = "[CHART] " if desc.get("is_chart") else "[IMAGE] "
            page_info = f"Page {desc['page']}: " if desc.get('page') else ""
            parts.append(f"\n{page_info}{chart_label}{desc['description']}")
        
        return "\n".join(parts)
    
    async def extract_images_from_html(
        self,
        html_content: str,
        source_id: str,
        base_url: str = ""
    ) -> List[ExtractedImage]:
        """Extract images from HTML content (web pages).
        
        Parses HTML for <img> tags, downloads images, and filters out
        small/decorative images. Returns list of ExtractedImage objects.
        
        v1.0.5: Added for web multimodal capture from browser extension.
        """
        try:
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin
            from PIL import Image
            
            soup = BeautifulSoup(html_content, 'html.parser')
            images = []
            
            # Find all img tags
            img_tags = soup.find_all('img')
            print(f"[MultimodalExtractor] Found {len(img_tags)} img tags in HTML")
            
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for idx, img_tag in enumerate(img_tags[:50]):  # Limit to first 50 images
                    try:
                        src = img_tag.get('src', '')
                        if not src:
                            continue
                        
                        image_bytes = None
                        
                        # Handle data URIs (inline base64 images)
                        if src.startswith('data:image'):
                            try:
                                # Parse data URI: data:image/png;base64,xxxxx
                                header, data = src.split(',', 1)
                                image_bytes = base64.b64decode(data)
                            except Exception:
                                continue
                        else:
                            # Handle relative and absolute URLs
                            if src.startswith('//'):
                                src = 'https:' + src
                            elif not src.startswith('http'):
                                if base_url:
                                    src = urljoin(base_url, src)
                                else:
                                    continue  # Can't resolve relative URL
                            
                            # Download image
                            try:
                                resp = await client.get(src)
                                if resp.status_code == 200:
                                    image_bytes = resp.content
                            except Exception as e:
                                print(f"[MultimodalExtractor] Failed to download {src[:50]}: {e}")
                                continue
                        
                        if not image_bytes or len(image_bytes) < 1000:  # Skip tiny files
                            continue
                        
                        # Get image dimensions
                        try:
                            img = Image.open(io.BytesIO(image_bytes))
                            width, height = img.size
                            img_format = img.format.lower() if img.format else 'png'
                        except Exception:
                            continue
                        
                        # Check if meaningful (not icon/bullet)
                        if not self._is_meaningful_image(width, height):
                            continue
                        
                        # Detect if chart
                        is_chart = self._detect_chart_heuristic(image_bytes)
                        
                        # Get alt text for context
                        alt_text = img_tag.get('alt', '')
                        
                        extracted = ExtractedImage(
                            image_bytes=image_bytes,
                            page_number=0,  # Web pages don't have page numbers
                            image_index=idx,
                            width=width,
                            height=height,
                            format=img_format,
                            is_chart=is_chart
                        )
                        
                        # Store alt text in description temporarily
                        if alt_text:
                            extracted.description = f"[Alt: {alt_text}] "
                        
                        images.append(extracted)
                        
                        if len(images) >= self.max_images_per_doc:
                            break
                            
                    except Exception as e:
                        print(f"[MultimodalExtractor] Error processing img tag: {e}")
                        continue
            
            print(f"[MultimodalExtractor] Extracted {len(images)} meaningful images from HTML")
            return images
            
        except ImportError as e:
            print(f"[MultimodalExtractor] Missing dependency for HTML extraction: {e}")
            return []
        except Exception as e:
            print(f"[MultimodalExtractor] HTML image extraction failed: {e}")
            return []
    
    async def extract_and_describe_html(
        self,
        html_content: str,
        source_id: str,
        base_url: str = "",
        page_title: str = ""
    ) -> List[Dict]:
        """Extract images from HTML and generate descriptions with parallel processing.
        
        Returns list of {description, is_chart, alt_text} for indexing.
        Uses parallel workers to speed up processing.
        
        v1.0.5: Added for web multimodal capture.
        """
        images = await self.extract_images_from_html(html_content, source_id, base_url)
        
        if not images:
            return []
        
        # Prioritize charts/graphs over regular images
        charts = [img for img in images if img.is_chart]
        regular = [img for img in images if not img.is_chart]
        prioritized = charts + regular
        
        # Limit to max_images_per_doc
        to_process = prioritized[:self.max_images_per_doc]
        
        print(f"[MultimodalExtractor] Processing {len(to_process)} web images ({len(charts)} charts)")
        
        # Create semaphore for parallel processing
        semaphore = asyncio.Semaphore(self.max_parallel_workers)
        
        # Process all images in parallel with rate limiting
        async def describe_web_image(image: ExtractedImage) -> Optional[Dict]:
            async with semaphore:
                # Include alt text in context if available
                context = page_title
                if image.description.startswith("[Alt:"):
                    context = f"{context} - {image.description}"
                
                description = await self.describe_image(image, context=context)
                if description:
                    # Prepend alt text if available
                    alt_prefix = image.description if image.description.startswith("[Alt:") else ""
                    return {
                        "page": 0,  # Web pages don't have page numbers
                        "description": f"{alt_prefix}{description}",
                        "is_chart": image.is_chart,
                        "size": f"{image.width}x{image.height}"
                    }
                return None
        
        tasks = [describe_web_image(image) for image in to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out None results and exceptions
        valid_results = [
            r for r in results 
            if r is not None and not isinstance(r, Exception)
        ]
        
        print(f"[MultimodalExtractor] Generated {len(valid_results)} web image descriptions")
        return valid_results


# Singleton instance
multimodal_extractor = MultimodalExtractor()
