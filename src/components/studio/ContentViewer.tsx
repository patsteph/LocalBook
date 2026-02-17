import React from 'react';
import ReactMarkdown from 'react-markdown';
import { contentService, ContentGeneration } from '../../services/content';

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
  const effectiveTopic = topic || 'the research content';

  return (
    <>
      {/* Generated Text Content */}
      {generatedContent && (
        <div className="border border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 rounded-lg p-4">
          <div className="flex justify-between items-center mb-3">
            <h4 className="font-medium text-sm text-green-900 dark:text-green-100">
              ✓ {contentSkillName} Generated
            </h4>
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  try {
                    await contentService.downloadAsPDF(generatedContent, contentSkillName, contentSkillName.toLowerCase().replace(/\s+/g, '-'));
                  } catch (err) {
                    console.error('PDF download failed:', err);
                  }
                }}
                className="text-xs px-2 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30"
              >
                📥 PDF
              </button>
              <button
                onClick={onClear}
                className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
              >
                Clear
              </button>
            </div>
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none max-h-96 overflow-y-auto bg-white dark:bg-gray-800 p-4 rounded border border-gray-200 dark:border-gray-600 prose-p:my-2 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-2 prose-li:my-0 prose-hr:my-4">
            <ReactMarkdown>{generatedContent}</ReactMarkdown>
          </div>
          {selectedSkill === 'feynman_curriculum' && (
            <div className="mt-3 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded-lg">
              <p className="text-sm font-medium text-blue-900 dark:text-blue-100 mb-2">Test Your Understanding</p>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => onQuizNav(effectiveTopic, 'easy')}
                  className="text-xs px-3 py-1.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 rounded-lg hover:bg-green-200 dark:hover:bg-green-900/50 border border-green-300 dark:border-green-700"
                >
                  Part 1: Foundation Quiz
                </button>
                <button
                  onClick={() => onQuizNav(effectiveTopic, 'medium')}
                  className="text-xs px-3 py-1.5 bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 rounded-lg hover:bg-yellow-200 dark:hover:bg-yellow-900/50 border border-yellow-300 dark:border-yellow-700"
                >
                  Part 2: Building Quiz
                </button>
                <button
                  onClick={() => onQuizNav(effectiveTopic, 'hard')}
                  className="text-xs px-3 py-1.5 bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 rounded-lg hover:bg-orange-200 dark:hover:bg-orange-900/50 border border-orange-300 dark:border-orange-700"
                >
                  Part 3: First Principles Quiz
                </button>
                <button
                  onClick={() => onQuizNav(effectiveTopic, 'hard')}
                  className="text-xs px-3 py-1.5 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50 border border-red-300 dark:border-red-700"
                >
                  Part 4: Mastery Quiz
                </button>
              </div>
            </div>
          )}
          {selectedSkill === 'feynman_curriculum' && (
            <div className="mt-3 p-3 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-700 rounded-lg">
              <p className="text-sm font-medium text-purple-900 dark:text-purple-100 mb-2">Visualize Your Learning</p>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => onVisualNav(`Learning progression for ${effectiveTopic}: show the 4-level Feynman journey from Foundation to Mastery with key concepts at each level`)}
                  className="text-xs px-3 py-1.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 rounded-lg hover:bg-green-200 dark:hover:bg-green-900/50 border border-green-300 dark:border-green-700"
                >
                  🎓 Learning Path
                </button>
                <button
                  onClick={() => onVisualNav(`Knowledge map for ${effectiveTopic}: show all core concepts, how they connect, why they work, and what's still unknown`)}
                  className="text-xs px-3 py-1.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-900/50 border border-blue-300 dark:border-blue-700"
                >
                  🧠 Knowledge Map
                </button>
                <button
                  onClick={() => onVisualNav(`Common misconceptions vs reality for ${effectiveTopic}: show what people commonly get wrong and why, with the key insight that resolves confusion`)}
                  className="text-xs px-3 py-1.5 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50 border border-red-300 dark:border-red-700"
                >
                  ❌➡️✅ Misconceptions
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Previous Document Generations */}
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
                onClick={() => onSelectContent(gen)}
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
