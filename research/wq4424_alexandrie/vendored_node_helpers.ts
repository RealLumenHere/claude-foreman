export const extractMetadataFromMarkdown = (markdown: string): { title: string; description?: string; tags?: string; content_clean: string } => {
  const metadata: { title: string; description?: string; tags?: string } = { title: '' };
  let contentClean = markdown;

  const metadataMatch = markdown.match(/^---\n([\s\S]*?)\n---/);
  if (metadataMatch && metadataMatch[1]) {
    const metadataContent = metadataMatch[1];
    const lines = metadataContent.split('\n');
    for (const line of lines) {
      const [key, ...rest] = line.split(':');
      const value = rest
        .join(':')
        .trim()
        .replace(/^"(.*)"$/, '$1'); // Remove surrounding quotes
      if (key === 'title') metadata.title = value;
      else if (key === 'description') metadata.description = value;
      else if (key === 'tags') metadata.tags = value;
    }
    contentClean = markdown.slice(metadataMatch[0].length).trimStart();
  }

  return { ...metadata, content_clean: contentClean };
};

export function parseTags(tags: string): string[] {
  if (typeof tags === 'string') {
    return tags
      .split(',')
      .map(tag => tag.trim())
      .filter(Boolean);
  }
  return [];
}
