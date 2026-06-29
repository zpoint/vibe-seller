export type FileKind = 'md' | 'delimited' | 'sheet' | 'pdf' | 'image' | 'binary' | 'text'

const KIND_BY_EXT: Record<string, FileKind> = {
  md: 'md', markdown: 'md',
  csv: 'delimited', tsv: 'delimited',
  xlsx: 'sheet', xls: 'sheet', xlsm: 'sheet',
  pdf: 'pdf',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image',
  zip: 'binary', gz: 'binary', tgz: 'binary', '7z': 'binary', rar: 'binary',
  docx: 'binary', pptx: 'binary',
}

export function fileKind(path: string): FileKind {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return KIND_BY_EXT[ext] ?? 'text'
}

/** Kinds whose content cannot come from the JSON text endpoint. */
export function isRawKind(kind: FileKind): boolean {
  return kind === 'sheet' || kind === 'pdf' || kind === 'image' || kind === 'binary'
}

export function rawUrl(path: string): string {
  return `/api/workspace/file/raw?path=${encodeURIComponent(path)}`
}
