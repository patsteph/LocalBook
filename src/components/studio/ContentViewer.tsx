import React from 'react';
import { ContentGeneration } from '../../services/content';
import { useCanvas } from '../canvas/CanvasContext';

interface ContentViewerProps {
  generatedContent: string;
  contentSkillName: string;
  selectedSkill: string;
  topic: string;
  selectedContentId: string | null;
  contentGenerations: ContentGeneration[];
  onClear: () => void;
  onSelectContent: (gen: ContentGeneration) => void;
  onDeleteContent: (contentId: string) => void;
  onQuizNav: (topic: string, difficulty: string) => void;
  onVisualNav: (content: string) => void;
}

export const ContentViewer: React.FC<ContentViewerProps> = ({
  generatedContent,
  contentSkillName,
  selectedSkill,
  topic,
  selectedContentId,
  contentGenerations,
  onClear,
  onSelectContent,
  onDeleteContent,
  onQuizNav,
  onVisualNav,
}) => {
  const ctx = useCanvas();
  const effectiveTopic = topic || 'the research content';

  return (
    <>
      {/* Compact success banner — content lives in the canvas overlay, not here */}
      {generatedContent && (
        <div className="border border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 rounded-lg p-3">
          <div className="flex items-center justify-between">
            <h4 className="font-medium text-sm text-green-900 dark:text-green-100">
              ✓ {contentSkillName} Generated
            </h4>
            <div className="flex items-center gap-2">
              <button
                onClick={() => ctx.openPanel('content-viewer', { content: generatedContent, title: contentSkillName })}
                className="text-xs px-2.5 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
              >
                View in Canvas
              </button>
              <button
                onClick={onClear}
                className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              >
                ✕
              </button>
            </div>
          </div>
          {selectedSkill === 'feynman_curriculum' && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              <button
                onClick={() => onQuizNav(effectiveTopic, 'easy')}
                className="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 rounded-lg hover:bg-green-200 dark:hover:bg-green-900/50"
              >
                Quiz: Foundation
              </button>
              <button
                onClick={() => onQuizNav(effectiveTopic, 'medium')}
                className="text-xs px-2 py-1 bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 rounded-lg hover:bg-yellow-200 dark:hover:bg-yellow-900/50"
              >
                Quiz: Building
              </button>
              <button
                onClick={() => onQuizNav(effectiveTopic, 'hard')}
                className="text-xs px-2 py-1 bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 rounded-lg hover:bg-orange-200 dark:hover:bg-orange-900/50"
              >
                Quiz: First Principles
              </button>
              <button
                onClick={() => onQuizNav(effectiveTopic, 'hard')}
                className="text-xs px-2 py-1 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50"
              >
                Quiz: Mastery
              </button>
              <button
                onClick={() => onVisualNav(`Learning progression for ${effectiveTopic}: show the 4-level Feynman journey from Foundation to Mastery with key concepts at each level`)}
                className="text-xs px-2 py-1 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded-lg hover:bg-purple-200 dark:hover:bg-purple-900/50"
              >
                🎓 Learning Path
              </button>
              <button
                onClick={() => onVisualNav(`Knowledge map for ${effectiveTopic}: show all core concepts, how they connect, why they work, and what's still unknown`)}
                className="text-xs px-2 py-1 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded-lg hover:bg-purple-200 dark:hover:bg-purple-900/50"
              >
                🧠 Knowledge Map
              </button>
            </div>
          )}
        </div>
      )}

      {/* Previous Document Generations — click opens in canvas */}
      {contentGenerations.length > 0 && (
        <div className="mt-4">
          <h4 className="font-medium text-sm mb-3 text-gray-900 dark:text-white">Previous Documents</h4>
          <div className="space-y-2">
            {contentGenerations.map((gen) => (
              <div
                key={gen.content_id}
                className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                  selectedContentId === gen.content_id
                    ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                }`}
                onClick={() => {
                  onSelectContent(gen);
                  ctx.openPanel('content-viewer', { content: gen.content, title: gen.skill_name });
                }}
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm text-gray-900 dark:text-white truncate">
                      {gen.skill_name}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {new Date(gen.created_at).toLocaleDateString()} • {gen.sources_used} sources
                    </p>
                    {gen.topic && (
                      <p className="text-xs text-gray-400 dark:text-gray-500 truncate mt-0.5">
                        Topic: {gen.topic}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteContent(gen.content_id);
                    }}
                    className="text-gray-400 hover:text-red-500 ml-2"
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
};
