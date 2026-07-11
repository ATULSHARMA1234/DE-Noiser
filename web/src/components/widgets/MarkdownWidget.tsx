import React from 'react';
import ReactMarkdown from 'react-markdown';

interface MarkdownWidgetProps {
 content: string;
}

export function MarkdownWidget({ content }: MarkdownWidgetProps) {
 return (
 <div className="h-full w-full overflow-y-auto p-4 text-[var(--text-primary)] prose prose-invert max-w-none">
 <ReactMarkdown>{content}</ReactMarkdown>
 </div>
 );
}
