"""Document processing service"""
import io
from typing import Dict
from pathlib import Path
from storage.source_store import source_store
from services.rag_engine import rag_service

class DocumentProcessor:
    """Process and ingest documents"""

    async def process(self, content: bytes, filename: str, notebook_id: str) -> Dict:
        """Process a document and add to RAG system"""

        # Extract text based on file type
        text = await self._extract_text(content, filename)

        # Create source record
        file_format = self._get_file_type(filename)
        source = await source_store.create(
            notebook_id=notebook_id,
            filename=filename,
            metadata={
                "type": file_format,
                "format": file_format,  # Frontend expects 'format'
                "size": len(content),
                "chunks": 0,
                "characters": 0,
                "status": "processing"
            }
        )

        # Ingest into RAG system
        try:
            result = await rag_service.ingest_document(
                notebook_id=notebook_id,
                source_id=source["id"],
                text=text,
                filename=filename,
                source_type=file_format
            )

            # Update source with processing results
            chunks = result.get("chunks", 0)
            characters = result.get("characters", len(text))

            # Save updated source back to store with content
            await source_store.update(notebook_id, source["id"], {
                "chunks": chunks,
                "characters": characters,
                "status": "completed",
                "content": text  # Save full text for viewing
            })

            # Update local copy for return
            source["chunks"] = chunks
            source["characters"] = characters
            source["status"] = "completed"

            return {
                "source_id": source["id"],
                "filename": filename,
                "format": source.get("format", source.get("type", "unknown")),
                "chunks": source["chunks"],
                "characters": source["characters"],
                "status": "completed"
            }
        except Exception as e:
            # Clean up source on failure
            await source_store.delete(notebook_id, source["id"])
            raise e

    async def _extract_text(self, content: bytes, filename: str) -> str:
        """Extract text from document"""
        file_type = self._get_file_type(filename)

        if file_type == "pdf":
            return await self._extract_from_pdf(content)
        elif file_type == "docx":
            return await self._extract_from_docx(content)
        elif file_type in ["xlsx", "xls"]:
            return await self._extract_from_excel(content, file_type)
        elif file_type == "csv":
            return await self._extract_from_csv(content)
        elif file_type in ["pptx", "ppt"]:
            return await self._extract_from_pptx(content)
        elif file_type in ["mp3", "wav", "m4a", "ogg", "flac", "aac", "wma"]:
            return await self._extract_from_audio(content, filename)
        elif file_type in ["mp4", "mov", "avi", "mkv", "webm", "wmv", "flv"]:
            return await self._extract_from_video(content, filename)
        elif file_type in ["txt", "md", "markdown", "json", "xml", "html", "htm", "py", "js", "ts", "css", "yaml", "yml"]:
            # Text-based files
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return content.decode('latin-1')
        else:
            # Try to decode as text, fallback to error
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError(f"Unsupported file type: {file_type}")

    async def _extract_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF"""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=content, filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            return text
        except Exception as e:
            raise ValueError(f"Failed to process PDF: {str(e)}")

    async def _extract_from_docx(self, content: bytes) -> str:
        """Extract text from DOCX"""
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            return text
        except Exception as e:
            raise ValueError(f"Failed to process DOCX: {str(e)}")

    async def _extract_from_excel(self, content: bytes, file_type: str) -> str:
        """Extract text from Excel files"""
        try:
            import pandas as pd
            
            # Read Excel file
            if file_type == "xlsx":
                df_dict = pd.read_excel(io.BytesIO(content), sheet_name=None, engine='openpyxl')
            else:  # xls
                df_dict = pd.read_excel(io.BytesIO(content), sheet_name=None, engine='xlrd')
            
            # Convert all sheets to text
            text_parts = []
            for sheet_name, df in df_dict.items():
                text_parts.append(f"=== Sheet: {sheet_name} ===\n")
                # Convert DataFrame to string, handling NaN values
                text_parts.append(df.fillna('').to_string(index=False))
                text_parts.append("\n\n")
            
            return "\n".join(text_parts)
        except ImportError:
            raise ValueError("Excel processing requires pandas and openpyxl. Install with: pip install pandas openpyxl xlrd")
        except Exception as e:
            raise ValueError(f"Failed to process Excel file: {str(e)}")

    async def _extract_from_csv(self, content: bytes) -> str:
        """Extract text from CSV files"""
        try:
            import pandas as pd
            
            # Try to detect encoding
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError:
                text = content.decode('latin-1')
            
            df = pd.read_csv(io.StringIO(text))
            return df.fillna('').to_string(index=False)
        except ImportError:
            # Fallback without pandas
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return content.decode('latin-1')
        except Exception as e:
            raise ValueError(f"Failed to process CSV file: {str(e)}")

    async def _extract_from_pptx(self, content: bytes) -> str:
        """Extract text from PowerPoint files"""
        try:
            from pptx import Presentation
            
            prs = Presentation(io.BytesIO(content))
            text_parts = []
            
            for slide_num, slide in enumerate(prs.slides, 1):
                text_parts.append(f"=== Slide {slide_num} ===")
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text:
                        text_parts.append(shape.text)
                text_parts.append("")
            
            return "\n".join(text_parts)
        except ImportError:
            raise ValueError("PowerPoint processing requires python-pptx. Install with: pip install python-pptx")
        except Exception as e:
            raise ValueError(f"Failed to process PowerPoint file: {str(e)}")

    async def _extract_from_audio(self, content: bytes, filename: str) -> str:
        """Extract text from audio files using speech-to-text"""
        import tempfile
        import os
        
        try:
            from faster_whisper import WhisperModel
            
            # Save to temp file (whisper needs file path)
            with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            
            try:
                # Load faster-whisper model (uses 'base' for speed, can use 'small' or 'medium' for accuracy)
                model = WhisperModel("base", device="cpu", compute_type="int8")
                segments, _ = model.transcribe(tmp_path)
                return " ".join([segment.text for segment in segments])
            finally:
                # Clean up temp file
                os.unlink(tmp_path)
                
        except ImportError:
            raise ValueError("Audio transcription requires faster-whisper. Install with: pip install faster-whisper")
        except Exception as e:
            raise ValueError(f"Failed to transcribe audio: {str(e)}")

    async def _extract_from_video(self, content: bytes, filename: str) -> str:
        """Extract text from video files by extracting audio and transcribing"""
        import tempfile
        import subprocess
        import os
        
        try:
            from faster_whisper import WhisperModel
            
            # Save video to temp file
            with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as tmp_video:
                tmp_video.write(content)
                video_path = tmp_video.name
            
            # Extract audio using ffmpeg
            audio_path = video_path + ".wav"
            
            try:
                subprocess.run(
                    ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-y", audio_path],
                    check=True,
                    capture_output=True,
                    timeout=300  # 5 min timeout for long videos
                )
                
                # Transcribe the extracted audio
                model = WhisperModel("base", device="cpu", compute_type="int8")
                segments, _ = model.transcribe(audio_path)
                return " ".join([segment.text for segment in segments])
                
            finally:
                # Clean up temp files
                if os.path.exists(video_path):
                    os.unlink(video_path)
                if os.path.exists(audio_path):
                    os.unlink(audio_path)
                    
        except ImportError:
            raise ValueError("Video transcription requires faster-whisper and ffmpeg. Install with: pip install faster-whisper")
        except subprocess.CalledProcessError:
            raise ValueError("Video processing requires ffmpeg. Install with: brew install ffmpeg")
        except Exception as e:
            raise ValueError(f"Failed to transcribe video: {str(e)}")

    def _get_file_type(self, filename: str) -> str:
        """Get file type from filename"""
        ext = Path(filename).suffix.lower()
        return ext[1:] if ext else "unknown"

document_processor = DocumentProcessor()
