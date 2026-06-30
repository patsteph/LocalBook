import { useCallback } from 'react';
import { emitEvent } from '../../lib/events';
import { visualService } from '../../services/visual';
import { localFetch, API_BASE_URL } from '../../services/api';
import { ChatMessage, InlineVisualData } from '../../types';

type SetMessages = React.Dispatch<React.SetStateAction<ChatMessage[]>>;

export function useVisualActions(
  notebookId: string | null,
  setMessages: SetMessages
) {
  // Generate inline visual for a message (with optional guidance for refinement, optional palette)
  const generateInlineVisual = useCallback(async (messageIndex: number, content: string, guidance?: string, palette?: string) => {
    if (!notebookId) return;
    
    // Mark as loading and clear previous alternatives
    const loadingMsg = guidance ? 'Analyzing your guidance...' : palette ? 'Applying new colors...' : 'Creating visual...';
    setMessages(prev => prev.map((m, i) => 
      i === messageIndex ? { ...m, visualLoading: true, visualLoadingMessage: loadingMsg, alternativeVisuals: [] } : m
    ));

    try {
      // Use streaming API for visual generation
      await visualService.generateSmartStream(
        notebookId,
        content,  // Full content - backend decides what to extract
        palette || 'auto',
        // onPrimary
        (diagram) => {
          const visual: InlineVisualData = {
            id: `inline-${messageIndex}-${Date.now()}`,
            type: diagram.svg ? 'svg' : 'mermaid',
            code: diagram.svg || diagram.code || '',
            title: diagram.title || 'Visual',
            template_id: diagram.template_id,
            pattern: diagram.diagram_type,
            tagline: diagram.tagline,
            // v2 extras for the inline thumbs + critic row
            v2_path: diagram.v2_path,
            v2_setup: diagram.v2_setup,
            v2_critic_score: diagram.v2_critic_score,
            v2_generation_ms: diagram.v2_generation_ms,
            notebookId: notebookId || undefined,
            originalPrompt: content,
          };
          
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, inlineVisual: visual, visualLoading: false } : m
          ));
        },
        // onAlternative
        (diagram) => {
          const altVisual: InlineVisualData = {
            id: `alt-${messageIndex}-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
            type: diagram.svg ? 'svg' : 'mermaid',
            code: diagram.svg || diagram.code || '',
            title: diagram.title || 'Alternative',
            template_id: diagram.template_id,
            pattern: diagram.diagram_type,
            tagline: diagram.tagline,
          };
          
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex 
              ? { ...m, alternativeVisuals: [...(m.alternativeVisuals || []), altVisual].slice(0, 3) }
              : m
          ));
        },
        // onDone
        () => {
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, visualLoading: false } : m
          ));
        },
        // onError
        (err: string) => {
          console.error('Inline visual generation failed:', err);
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, visualLoading: false } : m
          ));
        },
        undefined, // templateId
        guidance   // user refinement guidance
      );
    } catch (err) {
      console.error('Failed to generate inline visual:', err);
      setMessages(prev => prev.map((m, i) => 
        i === messageIndex ? { ...m, visualLoading: false } : m
      ));
    }
  }, [notebookId, setMessages]);

  // Open visual content in the universal canvas
  const openVisualInStudio = useCallback((content: string) => {
    emitEvent('openCanvasVisual', { content });
  }, []);

  // Save visual as Note (was: Save visual to Findings — Tier 5 refactor)
  const saveVisualToFindings = useCallback(async (visual: InlineVisualData) => {
    if (!notebookId || !visual) return;

    const title = visual.title || 'Saved Visual';
    const body = `# ${title}\n\nType: ${visual.type}\nTemplate: ${visual.template_id || 'auto'}\n\n\`\`\`${visual.type}\n${(visual.code || '').slice(0, 8000)}\n\`\`\``;

    try {
      // POST /sources/{nb}/note creates a real source (visible in the Sources
      // panel, RAG-indexed, searchable) rather than a canvas_note that only
      // lives in the canvas store and doesn't appear in Sources.
      await localFetch(`${API_BASE_URL}/sources/${notebookId}/note`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, content: body }),
      });
      emitEvent('sourcesUpdated');
      emitEvent('notesUpdated');
    } catch (err) {
      console.error('Failed to save visual as Note:', err);
    }
  }, [notebookId]);

  // Export visual as PNG or SVG
  const exportVisual = useCallback(async (visual: InlineVisualData, format: 'png' | 'svg') => {
    if (!visual || !visual.code) return;

    const filename = `${visual.title || 'visual'}-${Date.now()}`;

    if (format === 'svg') {
      let svgContent = visual.code;
      
      if (visual.type === 'mermaid') {
        const svgElement = document.querySelector('.mermaid svg');
        if (svgElement) {
          svgContent = svgElement.outerHTML;
        }
      }
      
      const blob = new Blob([svgContent], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${filename}.svg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } else {
      let svgContent = visual.code;
      
      if (visual.type === 'mermaid') {
        const svgElement = document.querySelector('.mermaid svg');
        if (svgElement) {
          svgContent = svgElement.outerHTML;
        }
      }

      const svgBlob = new Blob([svgContent], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      const img = new Image();
      
      img.onload = () => {
        const canvas = document.createElement('canvas');
        const scale = 2;
        canvas.width = img.width * scale;
        canvas.height = img.height * scale;
        
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.fillStyle = '#1e293b';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.scale(scale, scale);
          ctx.drawImage(img, 0, 0);
          
          canvas.toBlob((blob) => {
            if (blob) {
              const pngUrl = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = pngUrl;
              a.download = `${filename}.png`;
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);
              URL.revokeObjectURL(pngUrl);
            }
          }, 'image/png');
        }
        URL.revokeObjectURL(url);
      };
      
      img.onerror = () => {
        console.error('Failed to load SVG for PNG export');
        URL.revokeObjectURL(url);
      };
      
      img.src = url;
    }
  }, []);

  return {
    generateInlineVisual,
    openVisualInStudio,
    saveVisualToFindings,
    exportVisual,
  };
}
