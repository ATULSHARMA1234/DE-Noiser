import React from 'react';

type ShimmerProps = {
  className?: string;
  count?: number;
};

export function ShimmerSkeleton({ className = 'h-4 w-full', count = 1 }: ShimmerProps) {
  return (
    <div className="space-y-3 w-full">
      {Array.from({ length: count }).map((_, idx) => (
        <div
          key={idx}
          className={`shimmer-bg rounded-lg border border-white/5 opacity-80 ${className}`}
        />
      ))}
    </div>
  );
}

export function ShimmerCardList() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {Array.from({ length: 6 }).map((_, idx) => (
        <div key={idx} className="bg-white/[0.01] border border-white/5 rounded-2xl p-5 space-y-4">
          <div className="flex items-center gap-3">
            <ShimmerSkeleton className="h-10 w-10 rounded-xl shrink-0" />
            <div className="space-y-2 flex-1">
              <ShimmerSkeleton className="h-4 w-3/4" />
              <ShimmerSkeleton className="h-3 w-1/2" />
            </div>
          </div>
          <ShimmerSkeleton className="h-12 w-full mt-2" />
          <div className="flex justify-end pt-2">
            <ShimmerSkeleton className="h-8 w-20 rounded-lg" />
          </div>
        </div>
      ))}
    </div>
  );
}
