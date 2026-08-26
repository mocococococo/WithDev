import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type MarkdownContentProps = {
  content: string;
};

export function normalizeMarkdownContent(content: string) {
  const escapedLineBreaks = content.match(/\\r\\n|\\n|\\r/g)?.length ?? 0;
  const actualLineBreaks = content.match(/\r\n|\n|\r/g)?.length ?? 0;
  const normalizedLineBreaks =
    escapedLineBreaks > actualLineBreaks
      ? content.replace(/\\r\\n|\\n|\\r/g, '\n')
      : content;

  return normalizedLineBreaks.replace(
    /^(\s*(?:[-+*]|\d+[.)]))\u00a0+/gm,
    '$1 ',
  );
}

export default function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {normalizeMarkdownContent(content)}
    </ReactMarkdown>
  );
}
