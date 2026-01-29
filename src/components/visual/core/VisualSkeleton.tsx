/**
 * VisualSkeleton.tsx - Loading placeholder for visuals
 * 
 * Shows an animated skeleton while visual is generating.
 * Provides visual feedback during the generation process.
 */

import React from 'react';

export interface VisualSkeletonProps {
  compact?: boolean;
  className?: string;
  message?: string;
}

export const VisualSkeleton: React.FC<VisualSkeletonProps> = ({
  compact = false,
  className = '',
  message = 'Generating visual...',
}) => {
  const height = compact ? 'h-32' : 'h-64';

  return (
    <div 
      className={`visual-skeleton bg-gray-800 rounded-lg overflow-hidden ${height} ${className}`}
      role="status"
      aria-label="Loading visual"
    >
      {/* Animated gradient background */}
      <div className="relative w-full h-full">
        {/* Shimmer effect */}
        <div 
          className="absolute inset-0 bg-gradient-to-r from-gray-800 via-gray-700 to-gray-800"
          style={{
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s infinite linear',
          }}
        />
        
        {/* Skeleton structure */}
        <div className="relative z-10 p-4 flex flex-col h-full">
          {/* Title skeleton */}
          <div className="h-4 w-32 bg-gray-700 rounded mb-4 animate-pulse" />
          
          {/* Content skeleton - mimics a hub-spoke diagram */}
          <div className="flex-1 flex items-center justify-center">
            <div className="relative">
              {/* Center node */}
              <div className="w-16 h-16 bg-gray-700 rounded-full animate-pulse" />
              
              {/* Spoke nodes */}
              {!compact && (
                <>
                  <div className="absolute -top-12 left-1/2 -translate-x-1/2 w-12 h-8 bg-gray-700 rounded animate-pulse" style={{ animationDelay: '0.1s' }} />
                  <div className="absolute top-1/2 -right-16 -translate-y-1/2 w-12 h-8 bg-gray-700 rounded animate-pulse" style={{ animationDelay: '0.2s' }} />
                  <div className="absolute -bottom-12 left-1/2 -translate-x-1/2 w-12 h-8 bg-gray-700 rounded animate-pulse" style={{ animationDelay: '0.3s' }} />
                  <div className="absolute top-1/2 -left-16 -translate-y-1/2 w-12 h-8 bg-gray-700 rounded animate-pulse" style={{ animationDelay: '0.4s' }} />
                </>
              )}
            </div>
          </div>
          
          {/* Loading message */}
          <div className="flex items-center justify-center gap-2 text-gray-500 text-sm">
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span>{message}</span>
          </div>
        </div>
      </div>
      
      {/* Inject shimmer animation */}
      <style>{`
        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
      `}</style>
    </div>
  );
};

export default VisualSkeleton;
