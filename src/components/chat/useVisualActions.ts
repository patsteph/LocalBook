import { useCallback } from 'react';
import { visualService } from '../../services/visual';
import { findingsService } from '../../services/findings';
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

  // Open visual in Studio for full editing
  const openVisualInStudio = useCallback((content: string) => {
    sessionStorage.setItem('visualContent', content.substring(0, 2000));
    window.dispatchEvent(new CustomEvent('openStudioVisual', { 
      detail: { content: content.substring(0, 2000) } 
    }));
  }, []);

  // Save visual to Findings
  const saveVisualToFindings = useCallback(async (visual: InlineVisualData) => {
    if (!notebookId || !visual) return;
    
    try {
      await findingsService.saveVisual(
        notebookId,
        visual.title || 'Saved Visual',
        {
          type: visual.type,
          code: visual.code,
          template_id: visual.template_id,
        }
      );
      console.log('[Chat] Visual saved to Findings');
      window.dispatchEvent(new CustomEvent('findingsUpdated'));
    } catch (err) {
      console.error('Failed to save visual:', err);
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
      console.log('[Chat] Exported visual as SVG');
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
              console.log('[Chat] Exported visual as PNG');
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
